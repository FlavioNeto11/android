"""Prova REAL do 31.17: a sombra C0–C1 do curador no central, desde a partida com o envio aberto (T_on).

Só leitura e sem IA: o SQLite abre por URI `mode=ro` e com `PRAGMA query_only`; nada é escrito, nada é chamado. Lê
`decisao_fechada_sombra` (074 e 079), `learning_reviews` (069, o dossiê do curador) e `ai_calls` (as linhas do Jev, 31.14).

O que confere, e com que nível:

1. **Linhas da sombra** (PROVED, contagem no banco): desde o T_on só há a origem `curador`, só em `shadow`, só nas classes
   liberadas (`--classes`, padrão C0,C1); e o que a linha guarda é opaco: a pergunta da triagem, escolha e probabilidades
   só entre as opções `opt:*`, o `ref` como sha256 do dossiê.
2. **O corpo que saiu**: ele NÃO é guardado (074, ADR-069 item 5). O dossiê é: `learning_reviews.dossie` do mesmo
   `dossie_hash`. Quando o `content_hash` do dossiê guardado bate com o `ref` da linha (PROVED), o corpo é REMONTADO com o
   código deste checkout, pelo mesmo caminho da porta (`TriagemDoCurador.pedido` → `privacidade.validar` →
   `privacidade.redigir` → `{state, model, questions}` do transporte). Isso é INFERRED: vale para o código do commit em
   que o script roda, que precisa ser o implantado (`GET /api/health`, `commit`). Do corpo remontado, confere: só as três
   chaves; o `state` só com os `CAMPOS` do curador, valores curtos; e nenhum texto do dossiê fora do vocabulário (conteúdo,
   motivo de voto, ids citáveis, execuções, `origin_ref`, aparelho, versão), nem o `dossie_hash`, o `item_ref`, data ISO,
   uuid ou hex longo.
3. **Custo e latência** (PROVED): as linhas de `ai_calls` do Jev (`provider='jev'`, `origem='decisao_fechada'`), por
   chamada: US$ declarado, tokens, ms (p50, p95, máx.), falhas por motivo; e o cruzamento com a sombra (o US$ da sombra fica
   na primeira linha de cada chamada).

Uso, a partir da raiz do checkout implantado:

    backend/.venv/Scripts/python.exe scripts/jev-prova-31-17.py [--db data/poc.sqlite3] [--desde 2026-10-03T11:04:15Z]
        [--classes C0,C1] [--json saida.json]

Saída: o resumo curto na tela; `--json` grava tudo (só contagens, ids opacos e números: nada do dossiê). Código de saída 0
quando tudo confere, 1 com qualquer violação, 2 sem nenhuma linha da sombra no período.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

from app.modules.skills.domain.document import content_hash  # noqa: E402
from app.planning.decisao_fechada import privacidade  # noqa: E402
from app.planning.decisao_fechada.contrato import ID_NENHUMA  # noqa: E402
from app.planning.decisao_fechada.curador import CAMPOS, OPCOES, PERGUNTA_TRIAGEM, TriagemDoCurador  # noqa: E402
from app.planning.decisao_fechada.decisores import _pergunta_do_fio  # noqa: E402
from app.util import parse_iso, to_iso  # noqa: E402

#: A partida do backend com o envio aberto no deploy 9 (orquestradora, 03/10): o começo da sombra real do 31.17.
T_ON = "2026-10-03T11:04:15Z"
TEMPLATE_DO_CURADOR = "curador"
PROVEDOR_DO_JEV = "jev"
ORIGEM_NO_GASTO = "decisao_fechada"
OPCOES_VALIDAS = frozenset(OPCOES) | {ID_NENHUMA}
CHAVES_DO_CORPO = frozenset({"state", "model", "questions"})
ROTULO_MAX = 40
#: Texto do dossiê com menos que isto não entra na busca de vazamento: rótulos curtos (`for`, `licao`) aparecem de propósito.
TEXTO_MIN = 8
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DATA_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HEX_LONGO = re.compile(r"[0-9a-f]{24,}")
#: Rótulo de vocabulário fechado: uma palavra, sem espaço, curta. Só ele, num campo dos `CAMPOS`, escapa da busca de texto.
_ROTULO = re.compile(r"[\w-]{1,40}")
#: Chaves do dossiê cujo valor nunca pode sair (texto livre, id, data); as folhas delas entram na busca de vazamento.
_CHAVES_PROIBIDAS = frozenset({"conteudo", "motivo", "id", "origin_ref", "run_id", "aparelho", "app_version", "em",
                               "criado_em", "execucoes", "citaveis", "versao", "ref", "trail_ref", "item_ref", "titulo",
                               "nome", "texto", "descricao", "app", "capability", "razoes", "motivos"})


# ------------------------------------------------------------------ banco
def abrir(caminho: Path) -> sqlite3.Connection:
    """O SQLite SÓ LEITURA: URI `mode=ro` (abrir um caminho errado não cria arquivo) e `query_only` por cima."""
    if not caminho.is_file():
        raise SystemExit(f"banco não encontrado: {caminho}")
    conn = sqlite3.connect(f"{caminho.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _desde(valor: str) -> str:
    """O instante no formato de `to_iso` (`...SS.mmmZ`), para a comparação por texto não perder linha no mesmo segundo."""
    dt = parse_iso(valor)
    if dt is None:
        raise SystemExit(f"--desde inválido: {valor}")
    return to_iso(dt)


def _percentil(valores: Sequence[float], pct: int) -> float | None:
    """Posto mais próximo, `ceil(pct·n/100)` em aritmética inteira: o método do `sombra._p95` e do relatório do 31.10
    (31.19). Antes arredondava `q·(n−1)` e, com as 4 chamadas de 03/10, dava 510,2 ms de p50 onde o relatório dava 473,8."""
    if not valores:
        return None
    ordenados = sorted(valores)
    return round(ordenados[max(0, (pct * len(ordenados) + 99) // 100 - 1)], 1)


# ------------------------------------------------------------------ 1. linhas da sombra
def conferir_linhas(linhas: Sequence[Mapping[str, Any]], classes: frozenset[str]) -> dict[str, Any]:
    """Origem, modo e classe de cada linha, e o formato opaco do que ela guarda."""
    violacoes: Counter[str] = Counter()
    for linha in linhas:
        if linha["origem"] != "curador":
            violacoes[f"origem:{linha['origem']}"] += 1
        if linha["modo"] != "shadow":
            violacoes[f"modo:{linha['modo']}"] += 1
        if linha["classe"] not in classes:
            violacoes[f"classe:{linha['classe']}"] += 1
        if linha["origem"] != "curador":
            continue
        if linha["pergunta_id"] != PERGUNTA_TRIAGEM:
            violacoes["pergunta_fora_da_triagem"] += 1
        if linha["escolha"] is not None and linha["escolha"] not in OPCOES_VALIDAS:
            violacoes["escolha_fora_das_opcoes"] += 1
        try:
            probs = json.loads(linha["probabilidades"]) if linha["probabilidades"] else {}
        except ValueError:
            probs = None
        if not isinstance(probs, dict) or not set(probs) <= OPCOES_VALIDAS:
            violacoes["probabilidades_fora_das_opcoes"] += 1
        if not isinstance(linha["ref"], str) or not _SHA256.fullmatch(linha["ref"]):
            violacoes["ref_nao_e_sha256"] += 1
    return {
        "linhas": len(linhas),
        "por_origem_modo_classe": dict(Counter(f"{x['origem']}/{x['modo']}/{x['classe']}" for x in linhas)),
        "chamadas": len({x["chamada"] for x in linhas}),
        "respondidas": sum(1 for x in linhas if x["fallback_reason"] is None),
        "fallbacks": dict(Counter(x["fallback_reason"] for x in linhas if x["fallback_reason"] is not None)),
        "escolhas": dict(Counter(x["escolha"] or "(nenhuma escolha)" for x in linhas if x["fallback_reason"] is None)),
        "violacoes": dict(violacoes),
        "nivel": "PROVED",
    }


# ------------------------------------------------------------------ 2. o corpo remontado
def _folhas(valor: object, chave: str | None = None, proibida: bool = False) -> Iterable[str]:
    """As folhas de texto do dossiê que estão debaixo de uma chave proibida (ou de uma lista dela)."""
    if isinstance(valor, Mapping):
        for k, v in valor.items():
            yield from _folhas(v, str(k), proibida or str(k) in _CHAVES_PROIBIDAS)
    elif isinstance(valor, list):
        for v in valor:
            yield from _folhas(v, chave, proibida)
    elif isinstance(valor, str) and proibida:
        yield valor


def corpo_remontado(dossie: Mapping[str, object], dossie_hash: str, modelo: str,
                    classes: frozenset[str]) -> tuple[bytes | None, str | None]:
    """Os bytes que o transporte postaria para este dossiê, pelo caminho da porta; ou o motivo de nada sair."""
    pedido = TriagemDoCurador.pedido(dossie, dossie_hash)
    if pedido is None:
        return None, "fora_de_F1"
    veredito = privacidade.validar(replace(pedido, modo="shadow"), classes_yaml=classes)
    if not veredito.permitido:
        return None, f"privacidade:{veredito.motivo}"
    pronto = privacidade.redigir(pedido)
    perguntas = {p.id: _pergunta_do_fio(p) for p in pronto.perguntas if p.tipo == "choice"}
    corpo = json.dumps({"state": dict(pronto.estado), "model": modelo, "questions": perguntas}, ensure_ascii=False)
    return corpo.encode("utf-8"), None


def conferir_corpo(corpo: bytes, dossie: Mapping[str, object], *, dossie_hash: str,
                   item_ref: str | None) -> list[str]:
    """As violações do corpo remontado; vazio quando só sai o C0 do curador."""
    violacoes: list[str] = []
    dados = json.loads(corpo)
    if set(dados) != CHAVES_DO_CORPO:
        violacoes.append("chaves_do_corpo")
    estado = dados.get("state")
    if not isinstance(estado, dict) or not set(estado) <= CAMPOS:
        violacoes.append("campo_fora_dos_CAMPOS")
    elif any(not isinstance(v, str) or len(v) > ROTULO_MAX for v in estado.values()):
        violacoes.append("valor_do_estado_longo_ou_nao_texto")
    texto = corpo.decode("utf-8")
    if dossie_hash in texto:
        violacoes.append("dossie_hash_no_corpo")
    if item_ref and item_ref in texto:
        violacoes.append("item_ref_no_corpo")
    for nome, padrao in (("data_iso", _DATA_ISO), ("uuid", _UUID), ("hex_longo", _HEX_LONGO)):
        if padrao.search(texto):
            violacoes.append(f"{nome}_no_corpo")
    # Texto do dossiê só pode chegar pelo `state` (as perguntas são texto fixo do código): a busca é nele, para uma folha
    # que por acaso seja trecho da instrução não acusar. Uma folha igual a um rótulo do `state` (ex.: "published" no
    # conteúdo e no estado do item) sai pelo campo nomeado, não como vazamento do conteúdo.
    rotulos = ({v for k, v in estado.items() if k in CAMPOS and isinstance(v, str) and _ROTULO.fullmatch(v)}
               if isinstance(estado, dict) else set())
    texto_do_estado = json.dumps(estado, ensure_ascii=False)
    if any(len(f) >= TEXTO_MIN and f not in rotulos and f in texto_do_estado for f in _folhas(dossie)):
        violacoes.append("texto_do_dossie_no_corpo")
    return violacoes


def conferir_corpos(conn: sqlite3.Connection, refs: Iterable[str], modelo: str,
                    classes: frozenset[str]) -> dict[str, Any]:
    """Um corpo por `ref` (dossiê) da sombra. O dossiê só vale se o `content_hash` dele for o próprio `ref`."""
    contagem: Counter[str] = Counter()
    violacoes: Counter[str] = Counter()
    tamanhos: list[int] = []
    for ref in sorted(set(refs)):
        revisao = conn.execute(
            "SELECT item_ref, dossie FROM learning_reviews WHERE template_id=? AND dossie_hash=? AND dossie IS NOT NULL"
            " AND dossie NOT IN ('', '{}') ORDER BY created_at DESC, id DESC LIMIT 1", (TEMPLATE_DO_CURADOR, ref)).fetchone()
        if revisao is None:
            contagem["sem_dossie_guardado"] += 1
            continue
        try:
            dossie = json.loads(revisao["dossie"])
        except ValueError:
            contagem["dossie_ilegivel"] += 1
            continue
        if not isinstance(dossie, dict) or content_hash(dossie) != ref:
            contagem["hash_nao_confere"] += 1
            continue
        contagem["hash_confere"] += 1
        corpo, motivo = corpo_remontado(dossie, ref, modelo, classes)
        if corpo is None:
            contagem[f"nada_sairia:{motivo}"] += 1
            continue
        contagem["corpos_remontados"] += 1
        tamanhos.append(len(corpo))
        for v in conferir_corpo(corpo, dossie, dossie_hash=ref, item_ref=revisao["item_ref"]):
            violacoes[v] += 1
    return {"dossies": len(set(refs)), **dict(contagem), "bytes_max": max(tamanhos) if tamanhos else None,
            "violacoes": dict(violacoes), "nivel": "INFERRED (dossiê PROVED pelo hash; corpo remontado com este commit)"}


# ------------------------------------------------------------------ 3. custo e latência
def conferir_gasto(chamadas: Sequence[Mapping[str, Any]], linhas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ok = [c for c in chamadas if c["ok"]]
    ms = [float(c["ms"] or 0) for c in ok]
    usd_ai = sum(float(c["usd"] or 0) for c in chamadas)
    usd_sombra = sum(float(x["usd"] or 0) for x in linhas)
    fora_do_curador = sum(1 for c in chamadas if not str(c["ref"] or "").startswith("curador:"))
    com_etapa = sum(1 for c in chamadas if c["step_id"] is not None)
    return {
        "chamadas": len(chamadas), "ok": len(ok), "falhas": dict(Counter(c["error_kind"] or "?" for c in chamadas
                                                                         if not c["ok"])),
        "usd": round(usd_ai, 6), "usd_por_chamada": round(usd_ai / len(chamadas), 6) if chamadas else None,
        "usd_na_sombra": round(usd_sombra, 6),
        "tokens_entrada": sum(int(c["input_tokens"] or 0) for c in chamadas),
        "tokens_saida": sum(int(c["output_tokens"] or 0) for c in chamadas),
        "ms_p50": _percentil(ms, 50), "ms_p95": _percentil(ms, 95), "ms_max": round(max(ms), 1) if ms else None,
        "ms_media": round(statistics.fmean(ms), 1) if ms else None,
        "modelos": sorted({str(c["model"]) for c in chamadas}),
        "ref_fora_do_curador": fora_do_curador, "com_step_id": com_etapa,
        "nivel": "PROVED",
    }


# ------------------------------------------------------------------ montagem
def montar(conn: sqlite3.Connection, *, desde: str, classes: frozenset[str]) -> dict[str, Any]:
    linhas = conn.execute("SELECT * FROM decisao_fechada_sombra WHERE ts >= ? ORDER BY ts, id", (desde,)).fetchall()
    chamadas = conn.execute(
        "SELECT * FROM ai_calls WHERE ts >= ? AND provider=? AND origem=? ORDER BY ts, id",
        (desde, PROVEDOR_DO_JEV, ORIGEM_NO_GASTO)).fetchall()
    sombra = conferir_linhas(linhas, classes)
    modelo = str(chamadas[0]["model"]) if chamadas else "jev"
    corpos = conferir_corpos(conn, [str(x["ref"]) for x in linhas if x["origem"] == "curador" and x["ref"]], modelo,
                             classes)
    gasto = conferir_gasto(chamadas, linhas)
    # Cada chamada respondida ou que falhou DEPOIS do POST vira uma linha em `ai_calls`; as recusas antes do POST (privacidade,
    # orçamento, desligado) não. O cruzamento só aponta diferença: quem explica é o relatório.
    postadas = len({x["chamada"] for x in linhas if x["fallback_reason"] not in ("privacidade", "orcamento", "desligado")})
    problemas = [*(f"sombra:{k}" for k in sombra["violacoes"]), *(f"corpo:{k}" for k in corpos["violacoes"]),
                 *(["corpo:hash_nao_confere"] if corpos.get("hash_nao_confere") else []),
                 *(["gasto:ref_fora_do_curador"] if gasto["ref_fora_do_curador"] else []),
                 *(["gasto:com_step_id"] if gasto["com_step_id"] else [])]
    return {"desde": desde, "classes": sorted(classes), "sombra": sombra, "corpos": corpos, "gasto": gasto,
            "cruzamento": {"chamadas_postadas_na_sombra": postadas, "linhas_em_ai_calls": gasto["chamadas"],
                           "bate": postadas == gasto["chamadas"]},
            "problemas": problemas,
            "veredito": "SEM AMOSTRA" if not linhas else ("FALHOU" if problemas else "OK")}


def resumo(rel: Mapping[str, Any]) -> str:
    s, c, g, x = rel["sombra"], rel["corpos"], rel["gasto"], rel["cruzamento"]
    ms = f"p50 {g['ms_p50']} ms, p95 {g['ms_p95']} ms, máx. {g['ms_max']} ms" if g["ms_p50"] is not None else "sem latência"
    return "\n".join([
        f"Prova real do 31.17 desde {rel['desde']} (classes {', '.join(rel['classes'])}): {rel['veredito']}",
        f"- sombra (PROVED): {s['linhas']} linhas em {s['chamadas']} chamadas; {s['por_origem_modo_classe']};"
        f" respondidas {s['respondidas']}; fallbacks {s['fallbacks'] or 'nenhum'}; escolhas {s['escolhas'] or '—'};"
        f" violações {s['violacoes'] or 'nenhuma'}",
        f"- corpo (INFERRED, dossiê pelo hash): {c['dossies']} dossiês, hash confere {c.get('hash_confere', 0)},"
        f" sem dossiê {c.get('sem_dossie_guardado', 0)}, remontados {c.get('corpos_remontados', 0)}"
        f" (até {c['bytes_max']} B); violações {c['violacoes'] or 'nenhuma'}",
        f"- gasto (PROVED, ai_calls do Jev): {g['chamadas']} chamadas ({g['ok']} ok; falhas {g['falhas'] or 'nenhuma'});"
        f" US$ {g['usd']} (por chamada {g['usd_por_chamada']}; na sombra {g['usd_na_sombra']});"
        f" tokens {g['tokens_entrada']} + {g['tokens_saida']}; {ms}; modelos {g['modelos']}",
        f"- cruzamento: {x['chamadas_postadas_na_sombra']} chamadas postadas na sombra x {x['linhas_em_ai_calls']} linhas"
        f" em ai_calls ({'bate' if x['bate'] else 'NÃO bate'})",
        *([f"- problemas: {', '.join(rel['problemas'])}"] if rel["problemas"] else []),
    ])


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--db", default=str(RAIZ / "data" / "poc.sqlite3"))
    p.add_argument("--desde", default=T_ON, help=f"ISO-8601 UTC; padrão: o T_on do deploy 9 ({T_ON})")
    p.add_argument("--classes", default="C0,C1", help="as classes liberadas no YAML do central")
    p.add_argument("--json", help="arquivo do JSON completo")
    args = p.parse_args(argv)
    classes = frozenset(c.strip() for c in args.classes.split(",") if c.strip())
    conn = abrir(Path(args.db))
    try:
        rel = montar(conn, desde=_desde(args.desde), classes=classes)
    finally:
        conn.close()
    print(resumo(rel))
    if args.json:
        Path(args.json).write_text(json.dumps(rel, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return {"OK": 0, "FALHOU": 1}.get(rel["veredito"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
