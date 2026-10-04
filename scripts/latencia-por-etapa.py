"""Latência por etapa (item 31.24): onde uma execução gasta o tempo, só lendo o banco. Só números e ids.

Só leitura e sem IA: o SQLite abre por URI `mode=ro` e com `PRAGMA query_only`; nada é escrito, nada é chamado. Nenhum
texto de comando, nome ou valor é lido: das execuções só saem o id, os horários e o PREFIXO da `idempotency_key` (que
separa a bateria). Roda no esquema de antes da migração 088 (a tabela "antes" de docs/ia.md saiu dele) e no de depois;
o que a 088 acrescenta e o banco não tem aparece em `colunas_ausentes`, e a parte que depende dela sai vazia.

O que mede (os limites de cada coluna estão no cabeçalho da migração 088):

1. **IA por função e modelo**: p50/p95 de `ai_calls.ms` (a ida e volta ao provedor), com n.
2. **Parede das execuções**: de `created_at` a `finished_at`, em fases: antes do plano, plano, plano até a 1ª tentativa,
   dentro das tentativas (IA no caminho, ações no aparelho, resto), entre tentativas e cauda. As fases somam a parede.
   As execuções acima de `--longa-min` vão à parte (espera de pessoa, aprovação: não é latência do ciclo).
3. **Decisão → ação**: do fim do decide à intenção da 1ª ação, à ação concluída, e do fim de uma ação ao próximo decide
   (reobservar). Com a 088 o par vem de `actions.ai_call_id`; sem ela, por tentativa e ordem no tempo (inferido).
4. **Preparo da decisão** (088): settle, observação (árvore e imagem como partes), montagem do pedido, vaga de IA e
   "outros" por diferença até `started_at`.
5. **Juiz, verificação e evidência por tentativa** (088).
6. **Esperas por motivo** (088) e a aprovação (`pending_approvals`, de antes). Espera aberta fecha no fim do objetivo,
   ou no fim da janela.
7. **Execução pendurada** (derivada, nada a grava): o tempo da execução aberta sem tentativa, chamada de IA, ação nem
   espera aberta ("sem dono"). Acima de `--pendurada-min`, a execução sai listada pelo id. Sem a 088, a espera de pessoa
   ainda não se distingue da pendurada, e o relatório diz isso.
8. **Observação e captura**: os agregados de `measurements kind=metricas` (sem execução nem tentativa).

Uso, a partir da raiz do checkout:

    backend/.venv/Scripts/python.exe scripts/latencia-por-etapa.py [--db data/poc.sqlite3] [--horas 24 | --desde ISO]
        [--ate ISO] [--sem-bateria] [--json saida.json] [--md saida.md]

`--sem-bateria` tira as execuções cuja `idempotency_key` começa com `eval-` (a rodada QA pareada): até o contrato de
origem do 32.3 entrar, a separação é por esse prefixo. Saída: o markdown curto no stdout (ou `--md`); `--json` grava
tudo.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

from app.metricas import percentil as _posto_mais_proximo  # noqa: E402 - a MESMA regra de percentil do backend (K-085)
Linha = Mapping[str, object]
Intervalo = tuple[datetime, datetime]

#: (role, origem) de `ai_calls` → a função lida pelo dono.
FUNCOES: Final[dict[tuple[str, str | None], str]] = {
    ("plan", "execucao"): "planejador", ("decide", "execucao"): "ator", ("verify", "execucao"): "conferencia",
    ("plan", "curador"): "curador", ("decisao_fechada", "decisao_fechada"): "sombra", ("leitura", "leitura"): "leitor",
    ("social", "social"): "social", ("image", None): "imagem",
}
#: As funções que correm no relógio de uma execução (origem `execucao`).
NO_CAMINHO: Final = frozenset({"planejador", "ator", "conferencia"})
#: Ferramentas de contabilidade do ator: não tocam o aparelho (duram ~1 ms).
CONTABILIDADE: Final = frozenset({"step_done", "step_blocked"})
PREFIXO_DA_BATERIA: Final = "eval-"
#: O que a migração 088 acrescenta, por tabela. Ausente = banco de antes dela.
COLUNAS_088: Final[dict[str, tuple[str, ...]]] = {
    "ai_calls": ("started_at", "vaga_ms", "prep_settle_ms", "prep_observacao_ms", "prep_arvore_ms", "prep_imagem_ms",
                 "prep_prompt_ms"),
    "actions": ("ai_call_id",),
    "attempts": ("juiz_espera_ms", "verificacao_ms", "evidencia_ms"),
    "esperas": ("motivo", "inicio", "fim"),
}
FASES: Final = ("antes_do_plano", "plano", "plano_ate_1a_tentativa", "tent_ia", "tent_aparelho", "tent_resto",
                "entre_tentativas", "cauda")


# ------------------------------------------------------------------ utilitários
def abrir(caminho: Path) -> sqlite3.Connection:
    """O SQLite SÓ LEITURA: URI `mode=ro` (abrir um caminho errado não cria arquivo) e `query_only` por cima."""
    if not caminho.is_file():
        raise SystemExit(f"banco não encontrado: {caminho}")
    conn = sqlite3.connect(f"{caminho.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def instante(valor: object) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        d = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def percentil(valores: Sequence[float], q: float) -> float | None:
    """31.60: o posto mais próximo de `app.metricas.percentil` (K-085), o mesmo de `GET /api/desempenho`, da sombra e dos
    scripts do Jev: o valor é uma latência que aconteceu. Até aqui o script interpolava entre os postos, e a linha de base
    de 04/10 foi recalculada com esta regra para a comparação do 31.58 não misturar duas definições. `q` em fração."""
    return _posto_mais_proximo(sorted(valores), q * 100)


def dist(valores: Iterable[float | int | None]) -> dict[str, float | int | None]:
    xs = [float(v) for v in valores if v is not None]
    if not xs:
        return {"n": 0, "p50": None, "p95": None, "media": None, "soma": 0.0}
    return {"n": len(xs), "p50": percentil(xs, 0.5), "p95": percentil(xs, 0.95), "media": sum(xs) / len(xs),
            "soma": sum(xs)}


def mesclar(intervalos: Iterable[Intervalo]) -> list[Intervalo]:
    out: list[Intervalo] = []
    for a, b in sorted(i for i in intervalos if i[1] > i[0]):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def duracao(intervalos: Iterable[Intervalo]) -> float:
    return sum((b - a).total_seconds() for a, b in mesclar(intervalos))


def cortar(intervalos: Iterable[Intervalo], lo: datetime, hi: datetime) -> list[Intervalo]:
    return [(max(a, lo), min(b, hi)) for a, b in intervalos if b > lo and a < hi]


def intersecao(a: Iterable[Intervalo], b: Iterable[Intervalo]) -> list[Intervalo]:
    mb = mesclar(b)
    return [(max(x, y), min(z, w)) for x, z in mesclar(a) for y, w in mb if min(z, w) > max(x, y)]


def colunas(conn: sqlite3.Connection, tabela: str) -> set[str]:
    return {str(r["name"]) for r in conn.execute(f"PRAGMA table_info({tabela})")}


def classe_da_execucao(chave: object) -> str:
    """Só o prefixo da `idempotency_key`: o resto dela pode citar o comando, e não é lido."""
    k = chave if isinstance(chave, str) else ""
    if k.startswith(PREFIXO_DA_BATERIA):
        return "bateria"
    if k.startswith("validacao"):
        return "validacao"
    if k.startswith(("prova-", "apr-", "rerun-")):
        return "prova"
    return "outras"


# ------------------------------------------------------------------ leitura
@dataclass
class Dados:
    desde: datetime
    ate: datetime
    ausentes: list[str]
    runs: list[dict[str, object]] = field(default_factory=list)
    chamadas: list[dict[str, object]] = field(default_factory=list)
    tentativas: list[dict[str, object]] = field(default_factory=list)
    acoes: list[dict[str, object]] = field(default_factory=list)
    esperas: list[dict[str, object]] = field(default_factory=list)
    aprovacoes: list[dict[str, object]] = field(default_factory=list)
    metricas: list[str] = field(default_factory=list)
    classes: dict[str, int] = field(default_factory=dict)


def _linhas(conn: sqlite3.Connection, sql: str, params: Sequence[object]) -> list[dict[str, object]]:
    return [dict(r) for r in conn.execute(sql, tuple(params))]


def ler(conn: sqlite3.Connection, desde: datetime, ate: datetime, *, sem_bateria: bool) -> Dados:
    ausentes = [f"{t}.{c}" for t, cs in COLUNAS_088.items() for c in cs if c not in colunas(conn, t)]
    d = Dados(desde=desde, ate=ate, ausentes=ausentes)
    a, b = iso(desde)[:-1], iso(ate)[:-1]               # texto ISO ordena igual; sem o "Z" pega o legado sem fuso
    todas = _linhas(conn, "SELECT id, idempotency_key, status, created_at, started_at, finished_at FROM runs"
                          " WHERE created_at >= ? AND created_at < ?", (a, b))
    d.classes = dict(Counter(classe_da_execucao(r["idempotency_key"]) for r in todas))
    fora = {r["id"] for r in todas if sem_bateria and classe_da_execucao(r["idempotency_key"]) == "bateria"}
    d.runs = [{k: v for k, v in r.items() if k != "idempotency_key"}
              | {"classe": classe_da_execucao(r["idempotency_key"])} for r in todas if r["id"] not in fora]
    tem = lambda t, c: f"{t}.{c}" not in ausentes        # noqa: E731
    extras = ", ".join(c for c in COLUNAS_088["ai_calls"] if tem("ai_calls", c))
    d.chamadas = [c for c in _linhas(
        conn, "SELECT id, ts, run_id, attempt_id, role, origem, model, ms, ok" + (", " + extras if extras else "")
        + " FROM ai_calls WHERE ts >= ? AND ts < ?", (a, b)) if c["run_id"] not in fora]
    extras_t = ", ".join(f"t.{c}" for c in COLUNAS_088["attempts"] if tem("attempts", c))
    d.tentativas = [t for t in _linhas(
        conn, "SELECT t.id, t.step_id, s.run_id, t.strategy, t.status, t.started_at, t.finished_at"
        + (", " + extras_t if extras_t else "") + " FROM attempts t JOIN steps s ON s.id = t.step_id"
        " WHERE t.started_at >= ? AND t.started_at < ?", (a, b)) if t["run_id"] not in fora]
    d.acoes = [x for x in _linhas(
        conn, "SELECT a.id, a.attempt_id, s.run_id, a.tool, a.source, a.intent_at, a.done_at"
        + (", a.ai_call_id" if tem("actions", "ai_call_id") else "")
        + " FROM actions a JOIN attempts t ON t.id = a.attempt_id JOIN steps s ON s.id = t.step_id"
        " WHERE a.intent_at >= ? AND a.intent_at < ?", (a, b)) if x["run_id"] not in fora]
    if "esperas.motivo" not in ausentes:
        d.esperas = [e for e in _linhas(
            conn, "SELECT e.run_id, e.objective_id, e.motivo, e.inicio, e.fim, o.finished_at AS objetivo_fim"
            " FROM esperas e LEFT JOIN objectives o ON o.id = e.objective_id"
            " WHERE e.inicio < ? AND (e.fim IS NULL OR e.fim >= ?)", (b, a)) if e["run_id"] not in fora]
    if {"created_at", "decided_at", "objective_id"} <= colunas(conn, "pending_approvals"):
        d.aprovacoes = [x for x in _linhas(
            conn, "SELECT p.created_at, p.decided_at, o.run_id FROM pending_approvals p"
            " LEFT JOIN objectives o ON o.id = p.objective_id WHERE p.created_at >= ? AND p.created_at < ?", (a, b))
            if x["run_id"] not in fora]
    d.metricas = [str(r["data"]) for r in conn.execute(
        "SELECT data FROM measurements WHERE kind = 'metricas' AND ts >= ? AND ts < ?", (a, b))]
    return d


def funcao(c: Mapping[str, object]) -> str:
    origem = c.get("origem")
    return FUNCOES.get((str(c.get("role") or ""), origem if isinstance(origem, str) else None),
                       f"{c.get('role')}/{c.get('origem')}")


def intervalo_da_chamada(c: Mapping[str, object]) -> Intervalo | None:
    """Da entrega ao provedor (`started_at`, 088) até o fim (`ts`); sem a 088, `ts − ms` (só a ida e volta)."""
    fim = instante(c.get("ts"))
    if fim is None:
        return None
    ini = instante(c.get("started_at"))
    if ini is None:
        ms = c.get("ms")
        ini = fim - timedelta(milliseconds=float(ms) if isinstance(ms, (int, float)) else 0.0)
    return (ini, fim)


# ------------------------------------------------------------------ 1. IA por função e modelo
def ia_por_funcao(chamadas: Sequence[Mapping[str, object]]) -> dict[str, object]:
    por_f: dict[str, list[float]] = defaultdict(list)
    por_fm: dict[str, list[float]] = defaultdict(list)
    por_m: dict[str, list[float]] = defaultdict(list)
    falhas = Counter()
    for c in chamadas:
        ms = c.get("ms")
        if not c.get("ok"):
            falhas[funcao(c)] += 1
            continue
        if isinstance(ms, (int, float)):
            f, m = funcao(c), str(c.get("model") or "?")
            por_f[f].append(float(ms))
            por_fm[f"{f} · {m}"].append(float(ms))
            por_m[m].append(float(ms))
    return {"por_funcao": {k: dist(v) for k, v in por_f.items()},
            "por_funcao_e_modelo": {k: dist(v) for k, v in por_fm.items()},
            "por_modelo": {k: dist(v) for k, v in por_m.items()}, "falhas": dict(falhas)}


# ------------------------------------------------------------------ 2. parede em fases
def fases(d: Dados, *, longa_s: float) -> dict[str, object]:
    plano, ia = defaultdict(list), defaultdict(list)
    for c in d.chamadas:
        f = funcao(c)
        iv = intervalo_da_chamada(c)
        if iv is None or not c.get("run_id") or f not in NO_CAMINHO:
            continue
        (plano if f == "planejador" else ia)[str(c["run_id"])].append(iv)
    tents: dict[str, list[Intervalo]] = defaultdict(list)
    for t in d.tentativas:
        a, b = instante(t["started_at"]), instante(t["finished_at"])
        if a and b:
            tents[str(t["run_id"])].append((a, b))
    acoes: dict[str, list[Intervalo]] = defaultdict(list)
    for x in d.acoes:
        a, b = instante(x["intent_at"]), instante(x["done_at"])
        if a and b and x["tool"] not in CONTABILIDADE:
            acoes[str(x["run_id"])].append((a, b))
    grupos: dict[str, dict[str, list[float]]] = {"normais": defaultdict(list), "longas": defaultdict(list)}
    lacunas: list[tuple[float, str]] = []
    for r in d.runs:
        cr, fi = instante(r["created_at"]), instante(r["finished_at"])
        if cr is None or fi is None:
            continue
        rid = str(r["id"])
        parede = (fi - cr).total_seconds()
        pl = mesclar(cortar(plano[rid], cr, fi))
        at = mesclar(cortar(tents[rid], cr, fi))
        f = dict.fromkeys(FASES, 0.0)
        f["plano"] = duracao(pl)
        if pl:
            f["antes_do_plano"] = (pl[0][0] - cr).total_seconds()
        if at:
            f["plano_ate_1a_tentativa"] = ((at[0][0] - cr).total_seconds() - f["antes_do_plano"]
                                           - duracao(cortar(pl, cr, at[0][0])))
            dentro = duracao(at)
            f["tent_ia"] = duracao(intersecao(ia[rid], at))
            f["tent_aparelho"] = duracao(intersecao(acoes[rid], at))
            f["tent_resto"] = dentro - duracao(intersecao(ia[rid] + acoes[rid], at))
            f["entre_tentativas"] = ((at[-1][1] - at[0][0]).total_seconds() - dentro
                                     - duracao(intersecao(pl, [(at[0][0], at[-1][1])])))
            f["cauda"] = (fi - at[-1][1]).total_seconds()
            lacunas += [((y[0] - x[1]).total_seconds(), rid) for x, y in zip(at, at[1:])]
        else:
            f["cauda"] = parede - f["plano"] - f["antes_do_plano"]
        g = grupos["longas" if parede > longa_s else "normais"]
        g["parede"].append(parede)
        for k in FASES:
            g[k].append(f[k])
    saida: dict[str, object] = {}
    for nome, g in grupos.items():
        total = sum(g["parede"]) or 1.0
        saida[nome] = {"n": len(g["parede"]), "parede": dist(g["parede"]),
                       "fases": {k: {"total_s": sum(g[k]), "pct": 100 * sum(g[k]) / total, "por_execucao": dist(g[k])}
                                 for k in FASES}}
    saida["maiores_lacunas_entre_tentativas"] = [{"s": round(s, 1), "run_id": rid}
                                                 for s, rid in sorted(lacunas, reverse=True)[:5] if s > 5]
    return saida


# ------------------------------------------------------------------ 3. decisão → ação
def decisao_para_acao(d: Dados) -> dict[str, object]:
    decides = [c for c in d.chamadas if funcao(c) == "ator" and c.get("ok") and c.get("attempt_id")]
    por_tentativa: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for x in d.acoes:
        por_tentativa[str(x["attempt_id"])].append(x)
    pareado_por_id = "actions.ai_call_id" not in d.ausentes
    ate_intencao, ate_feito, reobservar = [], [], []
    sem_acao = 0
    inicios: dict[str, list[datetime]] = defaultdict(list)
    for c in decides:
        iv = intervalo_da_chamada(c)
        if iv is None:
            continue
        inicios[str(c["attempt_id"])].append(iv[0])
        candidatas = por_tentativa[str(c["attempt_id"])]
        if pareado_por_id:
            candidatas = [x for x in candidatas if x.get("ai_call_id") == c["id"]]
        candidatas = [x for x in candidatas
                      if (i := instante(x["intent_at"])) and i >= iv[1] - timedelta(milliseconds=50)]
        if not candidatas:
            sem_acao += 1
            continue
        x = min(candidatas, key=lambda y: str(y["intent_at"]))
        ate_intencao.append(((instante(x["intent_at"]) or iv[1]) - iv[1]).total_seconds() * 1000)
        if (feito := instante(x["done_at"])) is not None:
            ate_feito.append((feito - iv[1]).total_seconds() * 1000)
    for att, xs in por_tentativa.items():
        for x in xs:
            feito = instante(x["done_at"])
            if feito is None or x["tool"] in CONTABILIDADE:
                continue
            prox = [s for s in inicios.get(att, []) if s >= feito]
            if prox:
                reobservar.append((min(prox) - feito).total_seconds() * 1000)
    duracoes: dict[str, list[float]] = defaultdict(list)
    for x in d.acoes:
        a, b = instante(x["intent_at"]), instante(x["done_at"])
        if a and b:
            duracoes[str(x["tool"])].append((b - a).total_seconds() * 1000)
    return {"metodo": "actions.ai_call_id" if pareado_por_id else "tentativa e ordem no tempo (inferido)",
            "fim_do_decide_ate_intencao_ms": dist(ate_intencao), "fim_do_decide_ate_acao_feita_ms": dist(ate_feito),
            "fim_da_acao_ate_proximo_decide_ms": dist(reobservar), "decides_sem_acao": sem_acao,
            "duracao_da_acao_ms": {k: dist(v) for k, v in duracoes.items()}}


# ------------------------------------------------------------------ 4. preparo da decisão (088)
def preparo(d: Dados) -> dict[str, object]:
    if "ai_calls.prep_observacao_ms" in d.ausentes:
        return {}
    feitos: dict[str, list[datetime]] = defaultdict(list)
    for x in d.acoes:
        if (f := instante(x["done_at"])) is not None:
            feitos[str(x["attempt_id"])].append(f)
    inicio_da_tentativa = {str(t["id"]): instante(t["started_at"]) for t in d.tentativas}
    partes: dict[str, list[float]] = defaultdict(list)
    negativos = 0
    for c in d.chamadas:
        if funcao(c) != "ator" or c.get("prep_observacao_ms") is None or not c.get("attempt_id"):
            continue
        ini = instante(c.get("started_at"))
        antes = [f for f in feitos[str(c["attempt_id"])] if ini and f <= ini]
        ref = max(antes) if antes else inicio_da_tentativa.get(str(c["attempt_id"]))
        nums = {k: float(v) if isinstance(v, (int, float)) else 0.0
                for k, v in (("settle", c.get("prep_settle_ms")), ("observacao", c.get("prep_observacao_ms")),
                             ("prompt", c.get("prep_prompt_ms")), ("vaga", c.get("vaga_ms")))}
        for k, v in nums.items():
            partes[k].append(v)
        for k in ("arvore", "imagem"):
            if isinstance(v := c.get(f"prep_{k}_ms"), (int, float)):
                partes[k].append(float(v))
        if ini is not None and ref is not None:
            total = (ini - ref).total_seconds() * 1000
            outros = total - sum(nums.values())
            negativos += outros < -50                   # o relógio de parede e o monotônico discordam: contado
            partes["total"].append(total)
            partes["outros"].append(max(0.0, outros))
    return {"partes_ms": {k: dist(v) for k, v in partes.items()}, "outros_negativos": negativos}


# ------------------------------------------------------------------ 5. tentativa (088)
def tentativa(d: Dados) -> dict[str, object]:
    if "attempts.verificacao_ms" in d.ausentes:
        return {}
    por: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for t in d.tentativas:
        if t.get("verificacao_ms") is None:
            continue
        chave = f"{t.get('strategy') or '?'} {t.get('status')}"
        for k in ("juiz_espera_ms", "verificacao_ms", "evidencia_ms"):
            v = t.get(k)
            por[chave][k].append(float(v) if isinstance(v, (int, float)) else 0.0)
    return {k: {c: dist(v) for c, v in cols.items()} for k, cols in por.items()}


# ------------------------------------------------------------------ 6. esperas e aprovação
def esperas(d: Dados) -> dict[str, object]:
    por: dict[str, list[float]] = defaultdict(list)
    abertas = 0
    for e in d.esperas:
        a = instante(e["inicio"])
        b = instante(e["fim"]) or instante(e.get("objetivo_fim")) or d.ate
        abertas += e["fim"] is None
        if a is not None:
            por[str(e["motivo"])].append(max(0.0, (min(b, d.ate) - max(a, d.desde)).total_seconds()))
    aprov = [(instante(x["decided_at"]) or d.ate) - (instante(x["created_at"]) or d.ate) for x in d.aprovacoes]
    return {"por_motivo_s": {k: dist(v) for k, v in por.items()}, "abertas": abertas,
            "aprovacao_s": dist([x.total_seconds() for x in aprov])}


# ------------------------------------------------------------------ 7. pendurada (derivada)
def pendurada(d: Dados, *, limiar_s: float) -> dict[str, object]:
    cobertos: dict[str, list[Intervalo]] = defaultdict(list)
    for t in d.tentativas:
        a = instante(t["started_at"])
        if a:
            cobertos[str(t["run_id"])].append((a, instante(t["finished_at"]) or d.ate))
    for c in d.chamadas:
        if c.get("run_id") and (iv := intervalo_da_chamada(c)):
            cobertos[str(c["run_id"])].append(iv)
    for x in d.acoes:
        if (a := instante(x["intent_at"])) is not None:
            cobertos[str(x["run_id"])].append((a, instante(x["done_at"]) or a))
    for e in d.esperas:
        if (a := instante(e["inicio"])) is not None and e.get("run_id"):
            cobertos[str(e["run_id"])].append((a, instante(e["fim"]) or instante(e.get("objetivo_fim")) or d.ate))
    for x in d.aprovacoes:
        if (a := instante(x["created_at"])) is not None and x.get("run_id"):
            cobertos[str(x["run_id"])].append((a, instante(x["decided_at"]) or d.ate))
    sem_dono, listadas = [], []
    for r in d.runs:
        cr = instante(r["created_at"])
        if cr is None:
            continue
        fi = min(instante(r["finished_at"]) or d.ate, d.ate)
        livre = (fi - cr).total_seconds() - duracao(cortar(cobertos[str(r["id"])], cr, fi))
        sem_dono.append(livre)
        if livre >= limiar_s:
            listadas.append({"run_id": r["id"], "sem_dono_s": round(livre, 1), "aberta": r["finished_at"] is None})
    return {"sem_dono_s": dist(sem_dono), "limiar_s": limiar_s, "penduradas": listadas,
            "confiavel": "esperas.motivo" not in d.ausentes,
            "nota": "" if "esperas.motivo" not in d.ausentes else
            "sem a 088 a espera de pessoa (waiting_user) não aparece: ela conta aqui como sem dono"}


# ------------------------------------------------------------------ 8. observação e captura (agregados)
def observacao(d: Dados) -> dict[str, object]:
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for bruto in d.metricas:
        try:
            dados = json.loads(bruto)
        except ValueError:
            continue
        for x in dados.get("distribuicoes", []) if isinstance(dados, dict) else []:
            nome = str(x.get("nome", ""))
            if not nome.endswith(".ms"):
                continue
            r = x.get("rotulos") or {}
            k = f"{nome} {r.get('parte') or r.get('origem') or r.get('tipo')} {'worker' if r.get('via') else 'central'}"
            a = acc[k]
            a[0] += float(x.get("n") or 0)
            a[1] += float(x.get("soma") or 0)
            a[2] = max(a[2], float(x.get("p95") or 0))
    return {k: {"n": int(n), "media_ms": s / n if n else None, "soma_s": s / 1000, "p95_max_ms": p}
            for k, (n, s, p) in sorted(acc.items()) if n}


# ------------------------------------------------------------------ montagem e saída
def montar(conn: sqlite3.Connection, *, desde: datetime, ate: datetime, sem_bateria: bool, longa_s: float = 900,
           pendurada_s: float = 600) -> dict[str, object]:
    d = ler(conn, desde, ate, sem_bateria=sem_bateria)
    return {"janela": {"desde": iso(desde), "ate": iso(ate)}, "sem_bateria": sem_bateria,
            "colunas_ausentes": d.ausentes, "execucoes_por_classe": d.classes,
            "chamadas": {"total": len(d.chamadas), "falhas": sum(1 for c in d.chamadas if not c.get("ok"))},
            "ia": ia_por_funcao(d.chamadas), "parede": fases(d, longa_s=longa_s),
            "decisao_para_acao": decisao_para_acao(d), "preparo": preparo(d), "tentativa": tentativa(d),
            "esperas": esperas(d), "pendurada": pendurada(d, limiar_s=pendurada_s), "observacao": observacao(d)}


def _n(v: object, casas: int = 0) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def markdown(rel: Mapping[str, object]) -> str:
    j = rel["janela"]
    assert isinstance(j, dict)
    ausentes = rel["colunas_ausentes"]
    assert isinstance(ausentes, list)
    linhas = [f"Janela {j['desde']} → {j['ate']}; sem bateria: {rel['sem_bateria']}; execuções por classe: "
              f"{rel['execucoes_por_classe']}; colunas ausentes: {', '.join(ausentes) or 'nenhuma'}.",
              "", "| Função · modelo | n | p50 ms | p95 ms |", "|---|---|---|---|"]
    ia = rel["ia"]
    assert isinstance(ia, dict)
    for k, v in sorted(ia["por_funcao_e_modelo"].items()):
        linhas.append(f"| {k} | {v['n']} | {_n(v['p50'])} | {_n(v['p95'])} |")
    par = rel["parede"]
    assert isinstance(par, dict)
    for nome in ("normais", "longas"):
        g = par[nome]
        p = g["parede"]
        linhas += ["", f"**{nome}**: n={g['n']}, parede p50 {_n(p['p50'], 1)} s, p95 {_n(p['p95'], 1)} s",
                   "", "| Fase | total s | % | p50 s | p95 s |", "|---|---|---|---|---|"]
        for k, v in g["fases"].items():
            linhas.append(f"| {k} | {_n(v['total_s'])} | {_n(v['pct'], 1)} | {_n(v['por_execucao']['p50'], 1)} | "
                          f"{_n(v['por_execucao']['p95'], 1)} |")
    dpa = rel["decisao_para_acao"]
    assert isinstance(dpa, dict)
    linhas += ["", f"Decisão → ação ({dpa['metodo']}):", "", "| Intervalo | n | p50 ms | p95 ms |", "|---|---|---|---|"]
    for k in ("fim_do_decide_ate_intencao_ms", "fim_do_decide_ate_acao_feita_ms", "fim_da_acao_ate_proximo_decide_ms"):
        v = dpa[k]
        linhas.append(f"| {k} | {v['n']} | {_n(v['p50'])} | {_n(v['p95'])} |")
    prep = rel["preparo"]
    if isinstance(prep, dict) and prep:
        linhas += ["", "| Preparo da decisão | n | p50 ms | p95 ms | soma s |", "|---|---|---|---|---|"]
        for k, v in prep["partes_ms"].items():
            linhas.append(f"| {k} | {v['n']} | {_n(v['p50'])} | {_n(v['p95'])} | {_n(v['soma'] / 1000, 1)} |")
    esp = rel["esperas"]
    assert isinstance(esp, dict)
    if esp["por_motivo_s"]:
        linhas += ["", "| Espera | n | p50 s | p95 s | soma s |", "|---|---|---|---|---|"]
        for k, v in esp["por_motivo_s"].items():
            linhas.append(f"| {k} | {v['n']} | {_n(v['p50'], 1)} | {_n(v['p95'], 1)} | {_n(v['soma'])} |")
    pen = rel["pendurada"]
    assert isinstance(pen, dict)
    sd = pen["sem_dono_s"]
    linhas += ["", f"Sem dono por execução: p50 {_n(sd['p50'], 1)} s, p95 {_n(sd['p95'], 1)} s;"
               f" penduradas (≥ {_n(pen['limiar_s'])} s): {len(pen['penduradas'])}"
               + (f". {pen['nota']}" if pen["nota"] else "") + "."]
    return "\n".join(linhas) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    p.add_argument("--db", default=str(RAIZ / "data" / "poc.sqlite3"))
    p.add_argument("--horas", type=float, default=24.0, help="janela até --ate quando --desde não vem (padrão: 24)")
    p.add_argument("--desde", help="ISO-8601 UTC")
    p.add_argument("--ate", help="ISO-8601 UTC (padrão: agora)")
    p.add_argument("--sem-bateria", action="store_true", help="tira as execuções `eval-` (rodada QA pareada)")
    p.add_argument("--longa-min", type=float, default=15.0, help="execução acima disto vai à parte (padrão: 15)")
    p.add_argument("--pendurada-min", type=float, default=10.0,
                   help="sem dono acima disto lista a execução (padrão: 10)")
    p.add_argument("--json", help="arquivo do JSON completo (só números e ids)")
    p.add_argument("--md", help="arquivo do markdown curto (padrão: stdout)")
    a = p.parse_args(argv)
    ate = instante(a.ate) if a.ate else datetime.now(timezone.utc)
    if ate is None:
        raise SystemExit(f"--ate inválido: {a.ate}")
    desde = instante(a.desde) if a.desde else ate - timedelta(hours=a.horas)
    if desde is None or desde >= ate:
        raise SystemExit("janela inválida: --desde precisa ser ISO-8601 e anterior a --ate")
    with abrir(Path(a.db)) as conn:
        rel = montar(conn, desde=desde, ate=ate, sem_bateria=a.sem_bateria, longa_s=a.longa_min * 60,
                     pendurada_s=a.pendurada_min * 60)
    if a.json:
        Path(a.json).write_text(json.dumps(rel, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    texto = markdown(rel)
    if a.md:
        Path(a.md).write_text(texto, encoding="utf-8")
    else:
        sys.stdout.write(texto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
