"""Linha de base histórica de desempenho: agregados com p50/p95/n tirados do que o banco JÁ grava.

Existe porque "mais tarefas concluídas corretamente por hora, a menor custo" não se mede com média: `/api/usage`
só dava `avg_ms`, e uma cauda de 30 s escondida numa média de 4 s é exatamente o que decide se o parque anda. Aqui
nada é gravado — só se lê `runs/objectives/steps/attempts/actions`, `ai_calls`, `commands` e `measurements`.

Regras que não são detalhe:

- **Mede-se por entidade, nunca somando intervalos sobrepostos.** Dez objetivos de 60 s em paralelo são dez
  amostras de 60 s, não 600 s de "tempo gasto". Cada duração é de UMA linha (objetivo, etapa, ação, comando,
  chamada), e o que se agrega é a distribuição delas.
- **Falha e espera nunca somem.** As taxas de `succeeded`, `failed`, `uncertain`, `waiting_user` e `cancelled`
  saem separadas; "concluído corretamente" é só `succeeded`. O custo por objetivo concluído divide o gasto de
  TODAS as chamadas da coorte (plano, tentativas, falhas) pelos que deram certo.
- **Ausente é `None`, não zero.** Sem amostra não há p50; sem objetivo concluído não há custo por concluído; sem
  execução real não há US$ (o modo simulado não custa nada, e um modelo "simulado" não cadastrado seria cobrado
  pela tarifa MAIS CARA em `planning/costs.py`).
- **Intervalo negativo é contado, não descartado em silêncio** (`negativos`): relógio de convidado torto é um
  defeito conhecido (medições `clock`), e escondê-lo daria uma distribuição limpa e falsa.
- **Coorte explícita.** `objectives` não tem `created_at`; a população é "objetivos de execuções CRIADAS na
  janela" (`populacao` no resultado). Objetivo de execução criada antes da janela fica de fora, mesmo que termine
  dentro — efeito de borda aceito e declarado.
- **Dois dialetos.** Nada de `julianday`/`datetime()`: o SQL só filtra por texto ISO-8601 (que ordena igual no
  SQLite e no PostgreSQL) e as durações são calculadas aqui, em Python.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

from .db import Database, dumps, loads
from .metricas import percentil
from .planning import costs
from .util import now_iso, parse_iso

#: Status de objetivo que a coorte distingue. `pending`/`running` são "em andamento" — nem sucesso nem falha.
STATUS_OBJETIVO = ("succeeded", "failed", "uncertain", "waiting_user", "cancelled", "pending", "running")
#: Teto de ferramentas distintas na tabela de ações. O conjunto real é pequeno (as ferramentas do ator); passar
#: disto é sinal de nome livre, e o excedente vai para `_outras` em vez de explodir o payload.
MAX_FERRAMENTAS = 40
#: Teto de linhas `measurements(kind='irq')` por consulta. A sonda de saúde grava uma a cada 30 s por aparelho no
#: ar: dez aparelhos dão 1200 por hora, e o teto cobre ~16 h do parque inteiro ou dias de um aparelho só. Passado
#: dele ficam as MAIS RECENTES, e a resposta diz `truncada` — série cortada nunca passa por série inteira.
IRQ_MAX_LINHAS = 20_000
#: Campos de cada ponto da série. Os ticks acumulados e as vCPU ficam só no `ultimo`: na série inteira, dobrariam o
#: payload sem ajudar a ler a curva.
_IRQ_PONTO = ("ts", "irq_frac", "load1", "ocioso", "controle", "interesse")


# ---------------------------------------------------------------------------------------------- utilitários
def _instante(valor: Any) -> datetime | None:
    try:
        return parse_iso(str(valor)) if valor else None
    except ValueError:
        return None


def _segundos(inicio: Any, fim: Any) -> float | None:
    """`fim − início` em segundos; `None` quando falta uma ponta (desconhecido não é zero)."""
    a, b = _instante(inicio), _instante(fim)
    if a is None or b is None:
        return None
    return (b - a).total_seconds()


def _dist(valores: Iterable[float | None]) -> dict[str, Any]:
    """p50/p95 sobre TODOS os valores (não amostra), com `n`. Negativos contados à parte, fora da distribuição."""
    presentes = [float(v) for v in valores if v is not None]
    bons = sorted(v for v in presentes if v >= 0)
    out: dict[str, Any] = {
        "n": len(bons), "p50": percentil(bons, 50), "p95": percentil(bons, 95),
        "max": round(bons[-1], 3) if bons else None,
        "media": round(sum(bons) / len(bons), 3) if bons else None,
    }
    negativos = len(presentes) - len(bons)
    if negativos:
        out["negativos"] = negativos
    return out


def _taxas(contagem: dict[str, int], total: int) -> dict[str, float | None]:
    return {k: (round(v / total, 4) if total else None) for k, v in contagem.items()}


def _eh_simulado(provider: Any, run_simulated: Any) -> bool:
    return (provider or "") == "simulated" or bool(run_simulated)


# ---------------------------------------------------------------------------------------------- partes
def _objetivos(db: Any, desde: str, ate: str, horas: float | None, incluir_simulados: bool) -> tuple[dict[str, Any], int, int]:
    linhas = db.query(
        "SELECT o.status, o.started_at o_ini, o.finished_at o_fim, o.paused_s, o.wait_reason,"
        " r.id run_id, r.started_at r_ini, r.simulated, r.mode"
        " FROM objectives o JOIN runs r ON r.id = o.run_id WHERE r.created_at >= ? AND r.created_at < ?",
        (desde, ate))
    if not incluir_simulados:
        linhas = [ln for ln in linhas if not ln["simulated"]]
    contagem = {s: 0 for s in STATUS_OBJETIVO}
    outros = 0
    duracao: dict[str, list[float | None]] = defaultdict(list)
    fila: list[float | None] = []
    pausado: list[float | None] = []
    espera_atual: dict[str, int] = defaultdict(int)
    reais_ok = 0
    for ln in linhas:
        st = ln["status"]
        if st in contagem:
            contagem[st] += 1
        else:
            outros += 1
        if st == "succeeded" and not ln["simulated"]:
            reais_ok += 1
        if ln["o_ini"]:
            # Fila = do início da EXECUÇÃO (plano aprovado, objetivos materializados) ao início DESTE objetivo:
            # é a espera por vaga de aparelho, de IA ou de perfil. Não inclui o planejamento.
            fila.append(_segundos(ln["r_ini"], ln["o_ini"]))
        if st in ("succeeded", "failed", "uncertain", "waiting_user", "cancelled"):
            duracao[st].append(_segundos(ln["o_ini"], ln["o_fim"]))
            pausado.append(float(ln["paused_s"]) if ln["paused_s"] is not None else None)
        if st in ("pending", "running") and ln["wait_reason"]:
            espera_atual[str(ln["wait_reason"])] += 1
    total = len(linhas)
    run_linhas = db.query("SELECT id, created_at, started_at, simulated, mode FROM runs"
                          " WHERE created_at >= ? AND created_at < ?", (desde, ate))
    simuladas = sum(1 for r in run_linhas if r["simulated"])
    if not incluir_simulados:
        run_linhas = [r for r in run_linhas if not r["simulated"]]
    out = {
        "total": total,
        "por_status": contagem | ({"outros": outros} if outros else {}),
        "taxas": _taxas(contagem, total),
        # Só `succeeded` conta como concluído corretamente — `uncertain` e `waiting_user` não são sucesso.
        "concluidos_por_hora": round(contagem["succeeded"] / horas, 3) if horas else None,
        "fila_s": _dist(fila),
        "duracao_s": {st: _dist(v) for st, v in sorted(duracao.items())},
        # `started_at` é COALESCE e `finished_at` volta a NULL ao retomar: um objetivo que esperou pessoa e foi
        # retomado tem a espera DENTRO da duração. `paused_s` ao lado é o que permite descontar.
        "pausado_s": _dist(pausado),
        "espera_atual": dict(espera_atual),       # estado ATUAL (`wait_reason`), não histórico
        "execucoes": {
            "total": len(run_linhas),
            # Inclui o planejamento e, no modo `plan`, a aprovação de uma pessoa: não é latência de máquina.
            "criada_ate_inicio_s": _dist(_segundos(r["created_at"], r["started_at"]) for r in run_linhas),
            "por_modo": _contar(str(r["mode"]) for r in run_linhas),
        },
    }
    return out, simuladas, reais_ok


def _contar(valores: Iterable[str]) -> dict[str, int]:
    c: dict[str, int] = defaultdict(int)
    for v in valores:
        c[v] += 1
    return dict(c)


def _custo_da_coorte(db: Any, desde: str, ate: str, precos: dict[str, list[float]]) -> dict[str, Any]:
    """US$ de TODAS as chamadas das execuções da coorte (plano, decisões, verificações, tentativas que falharam).
    Só o que não é simulado entra: a chamada simulada não custa, e cobrada pela tabela sairia pela tarifa cheia."""
    reais = db.scalar("SELECT COUNT(*) FROM runs WHERE created_at >= ? AND created_at < ? AND simulated = 0",
                      (desde, ate)) or 0
    if not reais:
        return {"usd": None, "estimado": False, "execucoes_reais": 0}
    linhas = db.query(
        "SELECT a.model, SUM(a.input_tokens) input_tokens, SUM(a.cache_read) cache_read,"
        " SUM(a.cache_write) cache_write, SUM(a.output_tokens) output_tokens"
        " FROM ai_calls a JOIN runs r ON r.id = a.run_id"
        " WHERE r.created_at >= ? AND r.created_at < ? AND r.simulated = 0"
        " AND COALESCE(a.provider, '') <> 'simulated' GROUP BY a.model", (desde, ate))
    estimado = any(costs.effective_price(precos, ln["model"])[1] for ln in linhas)
    return {"usd": round(sum(costs.row_usd(precos, ln) for ln in linhas), 6), "estimado": estimado,
            "execucoes_reais": int(reais)}


def _ia(db: Any, desde: str, ate: str, precos: dict[str, list[float]], incluir_simulados: bool) -> dict[str, Any]:
    linhas = db.query(
        "SELECT a.role, a.model, a.provider, a.ms, a.ok, a.error_kind, a.fallback, a.requested_model,"
        " a.input_tokens, a.cache_read, a.cache_write, a.output_tokens, a.with_image, a.run_id,"
        " COALESCE(r.simulated, 0) run_simulated"
        " FROM ai_calls a LEFT JOIN runs r ON r.id = a.run_id WHERE a.ts >= ? AND a.ts < ?", (desde, ate))
    simuladas = sum(1 for ln in linhas if _eh_simulado(ln["provider"], ln["run_simulated"]))
    if not incluir_simulados:
        linhas = [ln for ln in linhas if not _eh_simulado(ln["provider"], ln["run_simulated"])]
    grupos: dict[tuple[str, str, str], dict[str, Any]] = {}
    por_papel: dict[str, list[float]] = defaultdict(list)
    fallbacks: dict[tuple[str, str, str], int] = defaultdict(int)
    erros: dict[str, int] = defaultdict(int)
    tokens = {"novo": 0, "cache_lido": 0, "cache_gravado": 0, "saida": 0}
    usd_total, usd_fora, estimado, algum_real = 0.0, 0.0, False, False
    for ln in linhas:
        # Modelo EFETIVAMENTE executado (`model` = quem respondeu, é por ele que se cobra); o pedido fica em
        # `requested_model` e só aparece na quebra de fallback.
        chave = (str(ln["role"]), str(ln["model"]), str(ln["provider"] or "?"))
        g = grupos.setdefault(chave, {"chamadas": 0, "erros": 0, "com_imagem": 0, "fallback": 0, "_ms": [],
                                      "_ms_erro": [], "tokens": {"novo": 0, "cache_lido": 0, "cache_gravado": 0,
                                                                 "saida": 0}, "usd": None, "preco_estimado": False})
        g["chamadas"] += 1
        g["com_imagem"] += int(ln["with_image"] or 0)
        t = (int(ln["input_tokens"] or 0), int(ln["cache_read"] or 0), int(ln["cache_write"] or 0),
             int(ln["output_tokens"] or 0))
        for nome, v in zip(("novo", "cache_lido", "cache_gravado", "saida"), t):
            g["tokens"][nome] += v
            tokens[nome] += v
        if ln["ok"]:
            g["_ms"].append(ln["ms"])
            por_papel[chave[0]].append(ln["ms"])
        else:
            g["erros"] += 1
            g["_ms_erro"].append(ln["ms"])
            erros[str(ln["error_kind"] or "error")] += 1
        if ln["fallback"]:
            g["fallback"] += 1
            fallbacks[(str(ln["fallback"]), str(ln["requested_model"] or "?"), str(ln["model"]))] += 1
        if not _eh_simulado(ln["provider"], ln["run_simulated"]):
            algum_real = True
            valor = costs.usd(precos, ln["model"], t)
            est = costs.effective_price(precos, ln["model"])[1]
            g["usd"] = round((g["usd"] or 0.0) + valor, 6)
            g["preco_estimado"] = g["preco_estimado"] or est
            estimado = estimado or est
            usd_total += valor
            if not ln["run_id"]:
                usd_fora += valor
    lista = []
    for (papel, modelo, provedor), g in sorted(grupos.items()):
        ms, ms_erro = g.pop("_ms"), g.pop("_ms_erro")
        lista.append({"papel": papel, "modelo": modelo, "provedor": provedor, **g, "ms": _dist(ms),
                      **({"ms_erros": _dist(ms_erro)} if ms_erro else {})})
    return {
        "chamadas": len(linhas),
        "erros": sum(erros.values()),
        "simuladas": simuladas,
        "com_imagem": sum(int(ln["with_image"] or 0) for ln in linhas),
        "tokens": tokens,
        "usd_total": round(usd_total, 6) if algum_real else None,
        "usd_fora_de_execucao": round(usd_fora, 6) if algum_real else None,   # social, treino, loja…
        "usd_estimado": estimado,
        "ms_por_papel": {p: _dist(v) for p, v in sorted(por_papel.items())},
        "por_papel_modelo": lista,
        "fallback": {"chamadas": sum(fallbacks.values()),
                     "por_motivo": [{"motivo": m, "pedido": p, "respondeu": r, "chamadas": n}
                                    for (m, p, r), n in sorted(fallbacks.items())]},
        "erros_por_tipo": dict(erros),
    }


def _etapas(db: Any, desde: str, ate: str, incluir_simulados: bool) -> dict[str, Any]:
    filtro = "" if incluir_simulados else " AND r.simulated = 0"
    linhas = db.query(
        "SELECT s.status, s.driven_by, s.started_at, s.finished_at, s.attempts FROM steps s"
        " JOIN runs r ON r.id = s.run_id WHERE r.created_at >= ? AND r.created_at < ?" + filtro, (desde, ate))
    por_driven: dict[str, list[float | None]] = defaultdict(list)
    tentativas: list[float | None] = []
    for ln in linhas:
        if ln["started_at"] and ln["finished_at"] and ln["status"] == "succeeded":
            por_driven[str(ln["driven_by"] or "ai")].append(_segundos(ln["started_at"], ln["finished_at"]))
        if ln["started_at"]:
            tentativas.append(float(ln["attempts"] or 0))
    return {"total": len(linhas), "por_status": _contar(str(ln["status"]) for ln in linhas),
            "duracao_s_por_driven_by": {k: _dist(v) for k, v in sorted(por_driven.items())},
            "tentativas_por_etapa": _dist(tentativas)}


def _acoes(db: Any, desde: str, ate: str, incluir_simulados: bool) -> dict[str, Any]:
    filtro = "" if incluir_simulados else " AND r.simulated = 0"
    linhas = db.query(
        "SELECT a.tool, a.status, a.source, a.intent_at, a.done_at FROM actions a"
        " JOIN attempts t ON t.id = a.attempt_id JOIN steps s ON s.id = t.step_id JOIN runs r ON r.id = s.run_id"
        " WHERE r.created_at >= ? AND r.created_at < ?" + filtro, (desde, ate))
    por_tool: dict[str, dict[str, Any]] = {}
    for ln in linhas:
        nome = str(ln["tool"])
        if nome not in por_tool and len(por_tool) >= MAX_FERRAMENTAS:
            nome = "_outras"
        g = por_tool.setdefault(nome, {"n": 0, "por_status": defaultdict(int), "por_origem": defaultdict(int),
                                       "_ms": []})
        g["n"] += 1
        g["por_status"][str(ln["status"])] += 1
        g["por_origem"][str(ln["source"] or "ai")] += 1
        s = _segundos(ln["intent_at"], ln["done_at"])
        g["_ms"].append(None if s is None else s * 1000)
    return {"total": len(linhas),
            "por_ferramenta": {k: {"n": g["n"], "por_status": dict(g["por_status"]),
                                   "por_origem": dict(g["por_origem"]), "ms": _dist(g["_ms"])}
                               for k, g in sorted(por_tool.items())}}


def _comandos(db: Any, desde: str, ate: str) -> dict[str, Any]:
    linhas = db.query("SELECT verb, state, created_at, dispatched_at, acked_at, finished_at FROM commands"
                      " WHERE created_at >= ? AND created_at < ?", (desde, ate))
    por_verbo: dict[str, dict[str, Any]] = {}
    for ln in linhas:
        g = por_verbo.setdefault(str(ln["verb"]), {"n": 0, "por_estado": defaultdict(int), "_fim": [],
                                                   "_despacho": [], "_ack": []})
        g["n"] += 1
        g["por_estado"][str(ln["state"])] += 1
        g["_fim"].append(_segundos(ln["created_at"], ln["finished_at"]))
        g["_despacho"].append(_segundos(ln["created_at"], ln["dispatched_at"]))
        g["_ack"].append(_segundos(ln["dispatched_at"], ln["acked_at"]))
    return {"total": len(linhas),
            "por_verbo": {v: {"n": g["n"], "por_estado": dict(g["por_estado"]),
                              "criado_ate_fim_s": _dist(g["_fim"]),
                              "criado_ate_despacho_s": _dist(g["_despacho"]),
                              "despacho_ate_ack_s": _dist(g["_ack"])}
                          for v, g in sorted(por_verbo.items())}}


def _aparelhos(db: Any, desde: str, ate: str) -> dict[str, Any]:
    linhas = db.query("SELECT kind, data FROM measurements WHERE kind IN ('boot', 'hibernate') AND ts >= ? AND ts < ?",
                      (desde, ate))
    boot: dict[str, list[float | None]] = defaultdict(list)
    hib: dict[str, list[float | None]] = defaultdict(list)
    for ln in linhas:
        d = loads(ln["data"], {}) or {}
        if ln["kind"] == "boot":
            boot[str(d.get("kind") or "?")].append(d.get("boot_seconds"))
        else:
            hib["salvo" if d.get("saved") else "nao_salvo"].append(d.get("save_seconds"))
    return {"boot_s": {k: _dist(v) for k, v in sorted(boot.items())},
            "hibernar_s": {k: _dist(v) for k, v in sorted(hib.items())}}


# ---------------------------------------------------------------------------------------------- entrada
def resumo(db: Any, *, desde_iso: str, precos: dict[str, list[float]], ate_iso: str | None = None,
           incluir_simulados: bool = False) -> dict[str, Any]:
    """Agregados de desempenho da janela `[desde_iso, ate_iso)` (fim padrão: agora).

    `incluir_simulados=False` (padrão) tira da coorte as execuções `simulated=1` e as chamadas do provedor
    simulado — e diz quantas tirou. Com `True` (a bancada simulada), elas entram nas contagens, mas NUNCA no US$.
    """
    ate = ate_iso or now_iso()
    a, b = _instante(desde_iso), _instante(ate)
    horas = (b - a).total_seconds() / 3600 if a and b and b > a else None
    objetivos, simuladas, reais_ok = _objetivos(db, desde_iso, ate, horas, incluir_simulados)
    custo = _custo_da_coorte(db, desde_iso, ate, precos)
    objetivos["usd_coorte"] = custo["usd"]
    objetivos["usd_estimado"] = custo["estimado"]
    # Custo por CONCLUÍDO: o gasto inteiro (falhas e tentativas incluídas) dividido só pelo que deu certo, e só
    # na parte real da coorte — um objetivo simulado não pode baratear a conta.
    objetivos["usd_por_concluido"] = (round(custo["usd"] / reais_ok, 6)
                                      if custo["usd"] is not None and reais_ok else None)
    return {
        "janela": {"desde": desde_iso, "ate": ate, "horas": round(horas, 3) if horas else None},
        "populacao": ("objetivos, etapas e ações de execuções CRIADAS na janela (runs.created_at); IA, comandos e "
                      "medições pelo próprio horário da linha"),
        "incluir_simulados": incluir_simulados,
        # Visível, não silencioso: quantas execuções simuladas a janela tinha e ficaram fora (0 quando incluídas).
        "execucoes_simuladas_excluidas": 0 if incluir_simulados else simuladas,
        "objetivos": objetivos,
        "etapas": _etapas(db, desde_iso, ate, incluir_simulados),
        "acoes": _acoes(db, desde_iso, ate, incluir_simulados),
        "ia": _ia(db, desde_iso, ate, precos, incluir_simulados),
        "comandos": _comandos(db, desde_iso, ate),
        "aparelhos": _aparelhos(db, desde_iso, ate),
    }


def interrupcoes(db: Database, *, desde_iso: str, aparelho: str | None = None,
                 limite: int = IRQ_MAX_LINHAS) -> dict[str, object]:
    """Fração de CPU do convidado em interrupção (irq + softirq), por aparelho, desde `desde_iso`: a série, o
    último valor e a distribuição. Lê o que `DeviceManager._gravar_interrupcao` grava a cada sonda de saúde.

    Existe para achar a causa do acúmulo (21–90% com dias no ar, ~2% depois do reinício a frio): a série se cruza
    com os acertos de relógio (`kind='clock'`) e com a prévia aberta (`interesse` no ponto). Aparelho sem linha na
    janela não aparece — sem medida não há "0%".
    """
    sql, params = "SELECT ts, data FROM measurements WHERE kind='irq' AND ts >= ?", [desde_iso]
    if aparelho:
        # `instance_id` mora no JSON, e JSON no SQL é dialeto. O LIKE, com o id codificado como o gravador o codifica
        # (`dumps`), só pré-filtra — `_` e `%` num id casariam a mais, e no SQLite o LIKE ignora caixa —, e a
        # igualdade exata é conferida abaixo, em Python.
        sql += " AND data LIKE ?"
        params.append(f'%"instance_id":{dumps(aparelho)}%')
    linhas = list(reversed(db.query(sql + " ORDER BY id DESC LIMIT ?", (*params, limite))))
    series: dict[str, list[dict[str, object]]] = defaultdict(list)
    fracoes: dict[str, list[float | None]] = defaultdict(list)
    ultimo: dict[str, dict[str, object]] = {}
    for ln in linhas:
        d = loads(ln["data"], {}) or {}
        iid, frac = d.get("instance_id"), d.get("irq_frac")
        if not isinstance(iid, str) or (aparelho and iid != aparelho) or not isinstance(frac, (int, float)):
            continue
        ponto = {"ts": ln["ts"], **{k: v for k, v in d.items() if k != "instance_id"}}
        series[iid].append({k: ponto.get(k) for k in _IRQ_PONTO})
        fracoes[iid].append(float(frac))
        ultimo[iid] = ponto
    return {
        "desde": desde_iso, "aparelho": aparelho,
        "n": sum(len(v) for v in series.values()),
        "truncada": len(linhas) >= limite,
        "por_aparelho": {iid: {"ultimo": ultimo[iid], "irq_frac": _dist(fracoes[iid]), "serie": serie}
                         for iid, serie in sorted(series.items())},
    }
