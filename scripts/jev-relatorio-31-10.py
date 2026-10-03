"""Relatório do 31.10: a sombra do Jev contra os limiares PRÉ-REGISTRADOS (`docs/design/jev-golden-set.md` §1–§3).

Só leitura e sem IA: lê `decisao_fechada_sombra` (074 e 079), `learning_reviews` (069; o curador e o rótulo de intenção do
30.25), `learning_transitions` (055), `runs` e `steps`. O banco abre em modo só leitura (`PRAGMA query_only` no SQLite,
`READ ONLY` no PostgreSQL). A única leitura fora do banco é o GET LOCAL de `/api/flows`, para o teto de cobertura da R2,
e `--sem-flows` a desliga. O relatório nunca liga nada: `on` é decisão registrada (ADR-069 item 6), com ele anexado.

Uso, a partir da raiz do checkout:

    backend/.venv/Scripts/python.exe scripts/jev-relatorio-31-10.py [--db data/poc.sqlite3 | --dsn postgresql://...]
        [--desde 2026-10-04T00:00:00Z] [--flows-url http://127.0.0.1:8000/api/flows | --sem-flows]
        [--json saida.json] [--md saida.md]

Níveis de cada número: `PROVED` é contagem no banco; `INFERRED` é derivado (taxa, rótulo pelo desfecho, braço de
controle). Abaixo do mínimo de rótulos do estrato, o relatório diz "sem amostra", nunca uma taxa (§1).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

from app.db import Database  # noqa: E402
from app.modules.learning.domain.intencao import (  # noqa: E402
    NENHUM, TEMPLATE_DO_ROTULO, item_ref_da_execucao, sucesso_comprovado,
)
from app.modules.learning.infrastructure.intencao_sql import RotulosSql  # noqa: E402
from app.planning.decisao_fechada.contrato import ID_NENHUMA  # noqa: E402
from app.planning.decisao_fechada.curador import (  # noqa: E402
    PERGUNTA_TRIAGEM, decisao_real_da_triagem, estado_do_dossie,
)
from app.planning.decisao_fechada.intencao import PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE, id_opaco  # noqa: E402

# ------------------------------------------------------------------ limiares PRÉ-REGISTRADOS (golden set; não mudar aqui)
#: §2, curador, por `kind`.
CURADOR = {"rotulos_min": 30, "acordo_min": 0.90, "erro_grave_max": 0.05, "cobertura_min": 0.80, "vantagem_min": 0.05}
#: §3, intenção, por app.
INTENCAO = {"rotulos_min": 50, "aceite_errado_max": 0.02, "precisao_min": 0.95}
#: §3: o Instagram não ganha data prevista; o GO dele se decide no relatório do 1º estrato.
SEM_DATA = frozenset({"instagram"})
#: A fatia diária do Jev no teto do dia (`limits.jev_max_usd_per_day`, 31.6).
FATIA_DO_DIA_USD = 0.50
TEMPLATE_DO_CURADOR = "curador"

#: Rótulo 1 do curador (§2): a decisão da PESSOA, pelo estado para onde ela levou o item. Publicar ou manter → `manter`;
#: rebaixar → `rebaixar`; desligar → `descartar` (os estados de `skills.domain.lifecycle.SkillState`).
ROTULO_DA_TRANSICAO = {"validated": "manter", "published": "manter", "candidate": "rebaixar", "deprecated": "rebaixar",
                       "disabled": "descartar"}
#: Rótulo 2 do curador: `resultado_posterior` (14 e 30 dias). Em 03/10 nenhum código o grava; quando gravar, só o que já
#: está na régua da triagem conta.
ROTULO_POSTERIOR = frozenset({"manter", "revisar", "rebaixar", "descartar"})


def _opt(rotulo: str) -> str:
    return f"opt:{rotulo}"


def _taxa(parte: int, todo: int) -> float | None:
    return round(parte / todo, 4) if todo else None


def _percentis(valores: Sequence[float]) -> dict[str, float | None]:
    if not valores:
        return {"p50": None, "p95": None}
    ordenados = sorted(valores)
    p95 = ordenados[min(len(ordenados) - 1, max(0, round(0.95 * len(ordenados)) - 1))]
    return {"p50": round(statistics.median(ordenados), 1), "p95": round(p95, 1)}


def _dia(ts: str) -> str:
    return ts[:10]


def _ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ------------------------------------------------------------------ leitura
def _linhas(db: Any, origem: str, perguntas: Iterable[str], desde: str | None) -> list[Mapping[str, Any]]:
    pergs = tuple(perguntas)
    marcas = ",".join("?" for _ in pergs)
    sql = (f"SELECT * FROM decisao_fechada_sombra WHERE origem=? AND pergunta_id IN ({marcas})"
           + (" AND ts>=?" if desde else "") + " ORDER BY ts, id")
    return db.query(sql, (origem, *pergs, *((desde,) if desde else ())))


def _valida(linha: Mapping[str, Any]) -> bool:
    """Respondeu: há escolha e não houve fallback. `nenhuma` é abstenção, conferida à parte."""
    return linha["escolha"] is not None and not linha["fallback_reason"]


def _respondeu(linha: Mapping[str, Any]) -> bool:
    return _valida(linha) and linha["escolha"] != ID_NENHUMA


def _custo(linhas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """US$ e tokens somam todas as linhas (só a primeira da chamada os carrega); a latência é por chamada."""
    por_chamada: dict[str, float] = {}
    por_dia: Counter[str] = Counter()
    for linha in linhas:
        por_chamada[str(linha["chamada"])] = max(por_chamada.get(str(linha["chamada"]), 0.0), float(linha["ms"] or 0))
        por_dia[_dia(str(linha["ts"]))] += float(linha["usd"] or 0)
    usd = round(sum(float(x["usd"] or 0) for x in linhas), 6)
    return {"nivel": "PROVED", "chamadas": len(por_chamada), "usd": usd,
            "tokens": sum(int(x["tokens"] or 0) for x in linhas), "ms": _percentis(list(por_chamada.values())),
            "usd_maior_dia": round(max(por_dia.values(), default=0.0), 6), "fatia_do_dia_usd": FATIA_DO_DIA_USD}


def _data_prevista(rotulos: int, minimo: int, primeiro: str | None, agora: datetime, sem_data: bool) -> str | None:
    """§1: o ritmo de rótulos medido até o mínimo. `None` sem ritmo; o texto fixo onde o golden set diz "sem data"."""
    if sem_data:
        return "sem data (golden set §3)"
    if rotulos >= minimo:
        return "mínimo alcançado"
    if not rotulos or primeiro is None:
        return None
    dias = max((agora - _ts(primeiro)).total_seconds() / 86400, 1 / 24)
    return (agora + timedelta(days=(minimo - rotulos) * dias / rotulos)).date().isoformat()


# ------------------------------------------------------------------ R1: curador
def _revisao_do_curador(db: Any, dossie_hash: str) -> Mapping[str, Any] | None:
    return db.one("SELECT * FROM learning_reviews WHERE template_id=? AND dossie_hash=? ORDER BY created_at DESC, id DESC"
                  " LIMIT 1", (TEMPLATE_DO_CURADOR, dossie_hash))


def _rotulo_do_curador(db: Any, revisao: Mapping[str, Any], desde_ts: str) -> tuple[str | None, str | None]:
    """(rótulo na régua da triagem, fonte). 1: a decisão da PESSOA no item depois da sombra; 2: `resultado_posterior`."""
    transicao = db.one("SELECT to_state FROM learning_transitions WHERE item_ref=? AND decided_by<>'sistema'"
                       " AND decided_at>=? ORDER BY decided_at, id LIMIT 1", (revisao["item_ref"], desde_ts))
    if transicao is not None and (rotulo := ROTULO_DA_TRANSICAO.get(str(transicao["to_state"]))):
        return rotulo, "pessoa"
    posterior = revisao["resultado_posterior"]
    if isinstance(posterior, str) and posterior in ROTULO_POSTERIOR:
        return posterior, "posterior"
    return None, None


def _controle_do_curador(revisao: Mapping[str, Any]) -> str:
    """Braço de controle (§2): a regra do adaptador simulado sobre o mesmo C0. Mais evidência contra que a favor →
    `revisar`; senão `manter`."""
    try:
        dossie = json.loads(revisao["dossie"] or "{}")
    except ValueError:
        dossie = {}
    estado = estado_do_dossie(dossie) if isinstance(dossie, dict) else {}
    contra, a_favor = int(estado.get("evidencias_contra") or 0), int(estado.get("evidencias_a_favor") or 0)
    return _opt("revisar") if contra > a_favor else _opt("manter")


def relatorio_do_curador(db: Any, desde: str | None, agora: datetime) -> dict[str, Any]:
    linhas = _linhas(db, "curador", (PERGUNTA_TRIAGEM,), desde)
    por_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for linha in linhas:
        revisao = _revisao_do_curador(db, str(linha["ref"] or ""))
        kind = str(revisao["item_kind"]) if revisao is not None else "desconhecido"
        rotulo, fonte = _rotulo_do_curador(db, revisao, str(linha["ts"])) if revisao is not None else (None, None)
        parecer = None
        if revisao is not None and revisao["saida"]:
            try:
                parecer = json.loads(revisao["saida"]).get("decisao")
            except (ValueError, AttributeError):
                parecer = None
        real = (decisao_real_da_triagem(parecer, validade=revisao["validade"], simulado=revisao["simulated"])
                if revisao is not None else None)
        por_kind[kind].append({"linha": linha, "rotulo": rotulo, "fonte": fonte, "real": real,
                               "controle": _controle_do_curador(revisao) if revisao is not None else None})
    estratos = {kind: _estrato_do_curador(itens, agora) for kind, itens in sorted(por_kind.items())}
    return {"linhas": len(linhas), "estratos": estratos, "custo": _custo(linhas),
            "nota": "concordância com o curador principal é acompanhamento, nunca GO sozinha (§2)"}


def _estrato_do_curador(itens: Sequence[dict[str, Any]], agora: datetime) -> dict[str, Any]:
    total = len(itens)
    respondidos = [i for i in itens if _respondeu(i["linha"])]
    rotulados = [i for i in respondidos if i["rotulo"]]
    acordo = sum(1 for i in rotulados if i["linha"]["escolha"] == _opt(i["rotulo"]))
    grave = sum(1 for i in rotulados if i["rotulo"] == "manter"
                and i["linha"]["escolha"] in (_opt("rebaixar"), _opt("descartar")))
    controle = sum(1 for i in rotulados if i["controle"] == _opt(i["rotulo"]))
    com_real = [i for i in respondidos if i["real"]]
    n = len(rotulados)
    medidas = {
        "pedidos": total, "respondidos": len(respondidos),
        "abstencoes": sum(1 for i in itens if _valida(i["linha"]) and i["linha"]["escolha"] == ID_NENHUMA),
        "fallbacks": dict(Counter(str(i["linha"]["fallback_reason"]) for i in itens if i["linha"]["fallback_reason"])),
        "rotulos": n, "rotulos_por_fonte": dict(Counter(i["fonte"] for i in rotulados)),
        "cobertura": _taxa(len(respondidos), total),
        "acordo": _taxa(acordo, n), "erro_grave": _taxa(grave, n), "acordo_controle": _taxa(controle, n),
        "concordancia_com_o_curador": _taxa(sum(1 for i in com_real if i["linha"]["escolha"] == i["real"]),
                                            len(com_real)),
        "com_parecer_valido": len(com_real),
    }
    if n < CURADOR["rotulos_min"]:
        veredito, falhou = "sem amostra", [f"rótulos {n} < {CURADOR['rotulos_min']}"]
    else:
        vantagem = (medidas["acordo"] or 0) - (medidas["acordo_controle"] or 0)
        medidas["vantagem"] = round(vantagem, 4)
        falhou = [c for c, ok in (
            (f"acordo {medidas['acordo']} < {CURADOR['acordo_min']}", (medidas["acordo"] or 0) >= CURADOR["acordo_min"]),
            (f"erro grave {medidas['erro_grave']} > {CURADOR['erro_grave_max']}",
             (medidas["erro_grave"] or 0) <= CURADOR["erro_grave_max"]),
            (f"cobertura {medidas['cobertura']} < {CURADOR['cobertura_min']}",
             (medidas["cobertura"] or 0) >= CURADOR["cobertura_min"]),
            (f"vantagem {round(vantagem, 4)} < {CURADOR['vantagem_min']}", vantagem >= CURADOR["vantagem_min"]),
        ) if not ok]
        veredito = "GO" if not falhou else "NO-GO"
    primeiro = str(itens[0]["linha"]["ts"]) if itens else None
    return {"medidas": medidas, "nivel": {"contagens": "PROVED", "taxas": "INFERRED", "acordo_controle": "INFERRED"},
            "veredito": veredito, "falhou": falhou, "modo": "on possível" if veredito == "GO" else "off",
            "data_prevista": _data_prevista(n, CURADOR["rotulos_min"], primeiro, agora, sem_data=False)}


# ------------------------------------------------------------------ R2 e R3: intenção
def _app_da_execucao(db: Any, run_id: str | None) -> str:
    if not run_id:
        return "sem_execucao"
    run = db.one("SELECT app_ids FROM runs WHERE id=?", (run_id,))
    try:
        apps = json.loads(run["app_ids"] or "[]") if run is not None else []
    except ValueError:
        apps = []
    return str(apps[0]) if apps else "sem_app"


def _rotulo_da_intencao(db: Any, rotulos: RotulosSql, linha: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """(id opaco do rótulo, fonte). 1: a escolha da PESSOA no rótulo do 30.25 (`nenhum` vira `nenhuma`); 2: o desfecho,
    a habilidade que a cadeia resolveu numa execução de sucesso comprovado (`sucesso_comprovado`, a regra do 30.25)."""
    run_id = linha["run_id"]
    if not run_id:
        return None, None
    humano = db.one("SELECT decisao_final FROM learning_reviews WHERE template_id=? AND item_ref=?"
                    " AND decisao_final IS NOT NULL AND (decidido_por IS NULL OR decidido_por<>'sistema')"
                    " ORDER BY created_at, id LIMIT 1", (TEMPLATE_DO_ROTULO, item_ref_da_execucao(str(run_id))))
    if humano is not None:
        escolha = str(humano["decisao_final"])
        return (ID_NENHUMA if escolha == NENHUM else id_opaco(escolha)), "pessoa"
    real = linha["decisao_real"]
    if linha["pergunta_id"] == PERGUNTA_CATALOGO and real and real != ID_NENHUMA:
        fatos = rotulos.fatos(str(run_id))
        if fatos is not None and sucesso_comprovado(fatos):
            return str(real), "desfecho"
    return None, None


def relatorio_da_intencao(db: Any, desde: str | None, agora: datetime,
                          flows: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    linhas = _linhas(db, "intencao", (PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE), desde)
    rotulos = RotulosSql(db)
    com_parametro = {id_opaco(f"flow:{f['id']}") for f in flows or () if "{" in str(f.get("command_template") or "")}
    por_app: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for linha in linhas:
        rotulo, fonte = _rotulo_da_intencao(db, rotulos, linha)
        por_app[_app_da_execucao(db, linha["run_id"])].append({"linha": linha, "rotulo": rotulo, "fonte": fonte})
    estratos = {app: _estrato_da_intencao(app, itens, agora, com_parametro) for app, itens in sorted(por_app.items())}
    return {"linhas": len(linhas), "estratos": estratos, "custo": _custo(linhas), "teto_da_r2": _teto_da_r2(flows)}


def _estrato_da_intencao(app: str, itens: Sequence[dict[str, Any]], agora: datetime,
                         com_parametro: set[str]) -> dict[str, Any]:
    r2 = [i for i in itens if i["linha"]["pergunta_id"] == PERGUNTA_CATALOGO]
    respondidos = [i for i in r2 if _respondeu(i["linha"])]
    rotulados = [i for i in r2 if _valida(i["linha"]) and i["rotulo"]]
    escolhas = [i for i in rotulados if i["linha"]["escolha"] != ID_NENHUMA]
    certas = [i for i in escolhas if i["linha"]["escolha"] == i["rotulo"]]
    # Métrica principal (RA-2): sem fluxo na cadeia (`sem_casamento` grava `nenhuma` como decisão real) e o Jev casou o
    # fluxo que o rótulo confirma.
    sem_fluxo = [i for i in rotulados if i["linha"]["decisao_real"] == ID_NENHUMA and i["rotulo"] != ID_NENHUMA]
    principal = [i for i in sem_fluxo if i["linha"]["escolha"] == i["rotulo"]]
    # Controle (a cadeia de hoje) só contra o rótulo da PESSOA: pelo desfecho, o rótulo É a decisão da cadeia (circular).
    da_pessoa = [i for i in rotulados if i["fonte"] == "pessoa"]
    controle = sum(1 for i in da_pessoa if i["linha"]["decisao_real"] == i["rotulo"])
    privacidade = Counter(str(i["linha"]["motivo_privacidade"] or "sem_motivo") for i in itens
                          if i["linha"]["fallback_reason"] == "privacidade")
    ambiguos = Counter(i["linha"]["ambiguos"] for i in r2 if i["linha"]["ambiguos"] is not None)
    n = len(rotulados)
    medidas = {
        "pedidos_r2": len(r2), "pedidos_r3": len(itens) - len(r2), "respondidos": len(respondidos),
        "abstencoes": sum(1 for i in r2 if _valida(i["linha"]) and i["linha"]["escolha"] == ID_NENHUMA),
        "cobertura": _taxa(len(respondidos), len(r2)),
        "fallbacks": dict(Counter(str(i["linha"]["fallback_reason"]) for i in itens if i["linha"]["fallback_reason"])),
        "recusas_por_privacidade": dict(privacidade),
        "rotulos": n, "rotulos_por_fonte": dict(Counter(i["fonte"] for i in rotulados)),
        "precisao": _taxa(len(certas), len(escolhas)), "aceite_errado": _taxa(len(escolhas) - len(certas), n),
        "acertos_em_fluxo_com_parametro": sum(1 for i in certas if i["linha"]["escolha"] in com_parametro),
        "principal_sem_fluxo_rotulados": len(sem_fluxo), "principal": len(principal),
        "acerto_controle_cadeia": _taxa(controle, len(da_pessoa)), "controle_sobre_rotulos_da_pessoa": len(da_pessoa),
        "ambiguos": {str(k): v for k, v in sorted(ambiguos.items())},
        "execucoes_com_ambiguidade": len({i["linha"]["run_id"] for i in r2 if (i["linha"]["ambiguos"] or 0) >= 1}),
    }
    if n < INTENCAO["rotulos_min"]:
        veredito, falhou = "sem amostra", [f"rótulos {n} < {INTENCAO['rotulos_min']}"]
    else:
        falhou = [c for c, ok in (
            (f"aceite errado {medidas['aceite_errado']} > {INTENCAO['aceite_errado_max']}",
             (medidas["aceite_errado"] or 0) <= INTENCAO["aceite_errado_max"]),
            (f"precisão {medidas['precisao']} < {INTENCAO['precisao_min']}",
             (medidas["precisao"] or 0) >= INTENCAO["precisao_min"]),
            (f"métrica principal {len(principal)} = 0", len(principal) > 0),
        ) if not ok]
        veredito = "GO" if not falhou else "NO-GO"
    primeiro = str(itens[0]["linha"]["ts"]) if itens else None
    return {"medidas": medidas, "nivel": {"contagens": "PROVED", "taxas": "INFERRED", "rotulo_desfecho": "INFERRED"},
            "veredito": veredito, "falhou": falhou, "modo": "sugerir possível" if veredito == "GO" else "off",
            "data_prevista": _data_prevista(n, INTENCAO["rotulos_min"], primeiro, agora, sem_data=app in SEM_DATA)}


def _teto_da_r2(flows: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """§3: uma `choice` só resolve SEM pergunta um fluxo sem `{parâmetro}`. O PRINCIPAL é o dos ativos."""
    if flows is None:
        return {"nivel": "not_run", "motivo": "sem /api/flows"}
    ativos = [f for f in flows if f.get("status") == "active"]

    def _sem_parametro(fs: Sequence[Mapping[str, Any]]) -> int:
        return sum(1 for f in fs if "{" not in str(f.get("command_template") or ""))

    return {"nivel": "PROVED", "ativos_sem_parametro": _sem_parametro(ativos), "ativos": len(ativos),
            "todos_sem_parametro": _sem_parametro(flows), "todos": len(flows)}


# ------------------------------------------------------------------ montagem e saída
def montar(db: Any, *, desde: str | None, agora: datetime,
           flows: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    return {"gerado_em": agora.isoformat().replace("+00:00", "Z"), "desde": desde,
            "limiares": {"curador": CURADOR, "intencao": INTENCAO, "fonte": "docs/design/jev-golden-set.md §1–§3"},
            "curador": relatorio_do_curador(db, desde, agora),
            "intencao": relatorio_da_intencao(db, desde, agora, flows),
            "regra": "o relatório nunca liga nada; `on` é decisão registrada (ADR-069 item 6)"}


def em_markdown(rel: Mapping[str, Any]) -> str:
    linhas = [f"# Relatório do 31.10 (sombra do Jev) — {rel['gerado_em']}", "",
              f"Janela: desde {rel['desde'] or 'o início'}. Limiares: {rel['limiares']['fonte']} (pré-registrados).",
              "Níveis: contagens PROVED (banco); taxas, rótulo pelo desfecho e controle INFERRED. "
              "Abaixo do mínimo de rótulos: \"sem amostra\".", ""]
    for nome, chave in (("Curador do Livro (R1), por kind", "curador"), ("Intenção (R2 e R3), por app", "intencao")):
        parte = rel[chave]
        linhas += [f"## {nome}", "", f"Linhas da sombra: {parte['linhas']}.", ""]
        for estrato, dados in parte["estratos"].items():
            m = dados["medidas"]
            linhas.append(f"### {estrato}: **{dados['veredito']}** ({dados['modo']})")
            if dados["falhou"]:
                linhas.append("Falhou: " + "; ".join(dados["falhou"]) + ".")
            linhas.append(f"Data prevista do GO: {dados['data_prevista'] or 'sem ritmo de rótulos'}.")
            linhas += [f"- {k}: {v}" for k, v in m.items()] + [""]
        c = parte["custo"]
        linhas += [f"Custo: {c['chamadas']} chamadas, US$ {c['usd']}, {c['tokens']} tokens, latência p50 "
                   f"{c['ms']['p50']} ms e p95 {c['ms']['p95']} ms; maior dia US$ {c['usd_maior_dia']} "
                   f"(fatia do dia US$ {c['fatia_do_dia_usd']}).", ""]
    teto = rel["intencao"]["teto_da_r2"]
    if teto.get("nivel") == "PROVED":
        linhas.append(f"Teto de cobertura da R2: {teto['ativos_sem_parametro']}/{teto['ativos']} fluxos ativos sem "
                      f"parâmetro (principal); {teto['todos_sem_parametro']}/{teto['todos']} de todos.")
    else:
        linhas.append("Teto de cobertura da R2: not_run (sem /api/flows).")
    return "\n".join(linhas + ["", rel["regra"] + "."]) + "\n"


class _SoLeitura(Database):
    """Só leitura em TODA conexão: a reconexão (`_reabrir`, achado #33) abre outra, que nasceria com escrita."""

    def _abrir(self) -> Any:
        conn = super()._abrir()
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY" if self.dialect == "postgres"
                     else "PRAGMA query_only=ON")
        return conn


def _abrir(args: argparse.Namespace) -> Database:
    """O banco em modo SÓ LEITURA. O SQLite precisa existir: abrir um caminho errado criaria um arquivo vazio."""
    if args.dsn:
        return _SoLeitura(args.dsn)
    caminho = Path(args.db)
    if not caminho.is_file():
        raise SystemExit(f"banco não encontrado: {caminho}")
    return _SoLeitura(caminho)


def _flows(url: str | None) -> list[dict[str, Any]] | None:
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=5) as resposta:   # noqa: S310 - GET local, só leitura
            dados = json.load(resposta)
    except (OSError, ValueError):
        return None
    return dados if isinstance(dados, list) else None


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--db", default=str(RAIZ / "data" / "poc.sqlite3"))
    p.add_argument("--dsn", help="PostgreSQL; no lugar de --db")
    p.add_argument("--desde", help="ISO-8601 UTC; padrão: o início da sombra")
    p.add_argument("--flows-url", default="http://127.0.0.1:8000/api/flows")
    p.add_argument("--sem-flows", action="store_true")
    p.add_argument("--json", help="arquivo do JSON; padrão: a tela")
    p.add_argument("--md", help="arquivo do Markdown")
    args = p.parse_args(argv)
    db = _abrir(args)
    try:
        rel = montar(db, desde=args.desde, agora=datetime.now(UTC),
                     flows=None if args.sem_flows else _flows(args.flows_url))
    finally:
        db.close()
    texto = json.dumps(rel, ensure_ascii=False, indent=1, default=str)
    if args.json:
        Path(args.json).write_text(texto, encoding="utf-8")
    else:
        print(texto)
    if args.md:
        Path(args.md).write_text(em_markdown(rel), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
