"""Leitura da sombra de INTENÇÃO (R2/R3, 31.9) no central depois do deploy 11: contagens, recusas e custo, sem texto.

Só leitura e sem IA: o SQLite abre por URI `mode=ro` e com `PRAGMA query_only`; nada é escrito, nada é chamado. Lê
`decisao_fechada_sombra` (074 e 079) e `ai_calls` (as linhas do Jev, 31.14). É irmão da prova do 31.17
(`jev-prova-31-17.py`), que olha o curador; aqui a origem é `intencao`, cujo corpo (o comando filtrado, C3) não é
guardado em lugar nenhum e por isso não se remonta.

O que mede, e com que nível:

1. **Contagens** (PROVED): todas as linhas desde `--desde` por origem/classe/modo; na intenção, por pergunta (R2 =
   `intencao_catalogo`, R3 = `intencao_desempate`): pedidos, respondidas, abstenções (`nenhuma`) e fallbacks, e quantos
   comandos (`run_id` distintos).
2. **Recusas** (PROVED): por `fallback_reason` e, nas de privacidade, por `motivo_privacidade`. Só os motivos, que são
   vocabulário fechado; o texto do comando nunca é lido.
3. **Confiança e probabilidade contra o limiar** (PROVED): a distribuição da `confianca` e a de `probabilidades[escolha]`
   (respondidas) e da maior probabilidade (todas), com a contagem acima do limiar da porta (o padrão de
   `contrato.pergunta_choice`, que a intenção usa). Desde o 31.19 a porta mede a probabilidade da escolha (ADR-069 item 20).
4. **Custo e latência** (PROVED): as linhas de `ai_calls` do Jev com `ref` `intencao:<run_id>`: chamadas, falhas, US$
   total e por chamada, tokens, ms p50/p95 pelo posto mais próximo (o mesmo dos outros scripts do Jev e do K-085).
   Desde a 083 (31.21), cada chamada da sombra diz se chegou ao POST (`postado`) e qual linha de `ai_calls` gerou
   (`ai_call_id`). Com isso a leitura separa a `rede` antes, depois e sem marca, conta a régua cega (POST sem linha de
   gasto) e cruza por id: um `ai_call_id` que não é linha do Jev da intenção em `ai_calls` REPROVA. As linhas sem marca
   (anteriores à 083) seguem só no cruzamento por contagem, que informa. O `--desde` precisa caber na retenção de
   `ai_calls` (`log_retention_days`, 14 dias de fábrica): linha purgada também não se acha.
5. **Zero texto** (falha fechado): cada coluna da linha só pode guardar o que o formato dela permite (ids opacos
   `opt:<12 hex>`, vocabulário fechado, números, o `run_id`). Qualquer coluna fora do esquema conhecido, valor fora do
   formato, JSON que não é {id: número} ou string com espaço conta como violação, e o valor nunca é impresso.

Uso, a partir da raiz do checkout implantado:

    backend/.venv/Scripts/python.exe scripts/jev-leitura-intencao.py [--db data/poc.sqlite3]
        [--desde 2026-10-03T15:30:00Z] [--json saida.json]

Saída: um resumo de 10 linhas; `--json` grava tudo (só contagens, motivos e números). Código de saída 0 quando tudo
confere, 1 com qualquer violação, 2 sem nenhuma linha da intenção no período.
"""
from __future__ import annotations

import argparse
import inspect
import json
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

from app.planning.decisao_fechada.contrato import (  # noqa: E402
    CLASSES, FALLBACKS, ID_NENHUMA, MODOS, MOTIVOS_DE_PRIVACIDADE, ORIGENS, pergunta_choice,
)
from app.planning.decisao_fechada.curador import OPCOES as OPCOES_DO_CURADOR  # noqa: E402
from app.planning.decisao_fechada.intencao import PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE  # noqa: E402
from app.planning.decisao_fechada.sombra import DESFECHOS  # noqa: E402
from app.util import parse_iso, to_iso  # noqa: E402

#: A partida do backend do deploy 11 (c8304e85, restart ~15:30Z, orquestradora): a intenção em sombra com C3. O instante
#: exato vem da orquestradora; `--desde` o substitui.
T_ON = "2026-10-03T15:30:00Z"
ORIGEM = "intencao"
PERGUNTAS = {PERGUNTA_CATALOGO: "R2", PERGUNTA_DESEMPATE: "R3"}
PROVEDOR_DO_JEV = "jev"
ORIGEM_NO_GASTO = "decisao_fechada"
#: As recusas antes do POST não viram linha em `ai_calls` (nada saiu). A exceção que a linha não distingue: a `rede` do
#: prazo esgotado antes do POST (`decisores.DecisorJev.decidir`) também não vira linha; por isso o cruzamento informa e
#: não reprova.
SEM_POST = frozenset({"privacidade", "orcamento", "desligado"})
#: O limiar que a porta aplica às perguntas da intenção: o padrão de `pergunta_choice`, lido do código.
LIMIAR = float(inspect.signature(pergunta_choice).parameters["limiar"].default)
#: Faixas da distribuição, fixas para comparar leituras de dias diferentes; a última começa no limiar.
FAIXAS = (0.50, 0.60, 0.70, LIMIAR)

#: As colunas que a sombra tem (074, 079 e 083). Coluna nova sem passar por aqui é violação: ninguém confere o que ela guarda.
COLUNAS = frozenset({"id", "ts", "chamada", "origem", "classe", "modo", "pergunta_id", "escolha", "probabilidades",
                     "confianca", "decisao_real", "desfecho", "usd", "tokens", "ms", "fallback_reason", "run_id",
                     "step_id", "ref", "ambiguos", "motivo_privacidade", "postado", "ai_call_id", "estado_hash"})
#: Rótulo da marca de POST de uma chamada (083): 1, 0 ou sem marca (NULO: não se sabe, ou linha anterior à 083).
SEM_MARCA = "sem_marca"
_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z")
_OPACO = re.compile(r"opt:[0-9a-f]{12}")
_ID = re.compile(r"[A-Za-z0-9_.:\-]{1,80}")
_REF = re.compile(r"[A-Za-z0-9_.:/#@\-]{1,200}")
#: O `estado_hash` da 086 (31.22): o sha256 do estado redigido, em hex minúsculo. Nunca o estado.
_HASH = re.compile(r"[0-9a-f]{64}")


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


def percentil(valores: Sequence[float], pct: int) -> float | None:
    """Posto mais próximo, `ceil(pct·n/100)` em aritmética inteira: o mesmo dos scripts do 31.10 e do 31.17 e do
    `metricas.percentil` depois do K-085."""
    if not valores:
        return None
    ordenados = sorted(valores)
    return round(ordenados[max(0, (pct * len(ordenados) + 99) // 100 - 1)], 3)


# ------------------------------------------------------------------ zero texto
def _opaco(valor: object, origem: object = ORIGEM) -> bool:
    """Id de opção: no curador, uma das opções da triagem; nas outras origens, `opt:<12 hex>` (o `id_opaco`)."""
    if not isinstance(valor, str):
        return False
    if valor == ID_NENHUMA:
        return True
    return valor in OPCOES_DO_CURADOR if origem == "curador" else bool(_OPACO.fullmatch(valor))


def _probabilidades(bruto: object, origem: object = ORIGEM) -> dict[str, float] | None:
    """O JSON {id opaco: número em [0, 1]} da linha; `None` quando é outra coisa (e a linha viola)."""
    if bruto is None:
        return {}
    try:
        probs = json.loads(bruto) if isinstance(bruto, str) else None
    except ValueError:
        return None
    if not isinstance(probs, dict):
        return None
    if not all(_opaco(k, origem) and isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= float(v) <= 1.0
               for k, v in probs.items()):
        return None
    return {str(k): float(v) for k, v in probs.items()}


def _inteiro(valor: object) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def violacoes_da_linha(linha: Mapping[str, Any]) -> list[str]:
    """O que a linha guarda fora do formato da coluna. Devolve só o NOME da violação, nunca o valor."""
    v: list[str] = [f"coluna_desconhecida:{c}" for c in linha.keys() if c not in COLUNAS]
    d = dict(linha)
    postado, ai_call_id = d.get("postado"), d.get("ai_call_id")      # ausentes num banco anterior à 083
    estado_hash = d.get("estado_hash")                               # ausente num banco anterior à 086
    # a marca da 083 tem de ser coerente: sem POST não há linha de gasto nem resposta
    if postado == 0 and ai_call_id is not None:
        v.append("marca:id_sem_post")
    if postado == 0 and d["fallback_reason"] is None:
        v.append("marca:resposta_sem_post")
    for coluna, valor in dict(linha).items():
        # o JSON das probabilidades tem espaço pelo `json.dumps` (", " e ": "); ele é conferido pela estrutura, abaixo
        if coluna != "probabilidades" and isinstance(valor, str) and (any(ch.isspace() for ch in valor)
                                                                     or len(valor) > 200):
            v.append(f"texto_em:{coluna}")
    regras: tuple[tuple[str, bool], ...] = (
        ("ts", isinstance(linha["ts"], str) and bool(_TS.fullmatch(linha["ts"]))),
        ("chamada", isinstance(linha["chamada"], str) and bool(_ID.fullmatch(linha["chamada"]))),
        ("origem", linha["origem"] in ORIGENS),
        ("classe", linha["classe"] in CLASSES),
        ("modo", linha["modo"] in MODOS),
        ("pergunta_id", linha["pergunta_id"] in PERGUNTAS if linha["origem"] == ORIGEM
         else isinstance(linha["pergunta_id"], str) and bool(_ID.fullmatch(linha["pergunta_id"]))),
        ("escolha", linha["escolha"] is None or _opaco(linha["escolha"], linha["origem"])),
        ("decisao_real", linha["decisao_real"] is None or _opaco(linha["decisao_real"], linha["origem"])),
        ("probabilidades", _probabilidades(linha["probabilidades"], linha["origem"]) is not None),
        ("desfecho", linha["desfecho"] is None or linha["desfecho"] in DESFECHOS),
        ("fallback_reason", linha["fallback_reason"] is None or linha["fallback_reason"] in FALLBACKS),
        ("motivo_privacidade", linha["motivo_privacidade"] is None
         or linha["motivo_privacidade"] in MOTIVOS_DE_PRIVACIDADE),
        ("run_id", linha["run_id"] is None or (isinstance(linha["run_id"], str) and bool(_REF.fullmatch(linha["run_id"])))),
        ("step_id", linha["step_id"] is None or (isinstance(linha["step_id"], str) and bool(_ID.fullmatch(linha["step_id"])))),
        ("ref", linha["ref"] is None or (isinstance(linha["ref"], str) and bool(_REF.fullmatch(linha["ref"])))),
        ("confianca", linha["confianca"] is None or (isinstance(linha["confianca"], (int, float))
                                                     and 0.0 <= float(linha["confianca"]) <= 1.0)),
        ("ambiguos", linha["ambiguos"] is None or (isinstance(linha["ambiguos"], int) and linha["ambiguos"] >= 0)),
        ("postado", postado is None or (_inteiro(postado) and postado in (0, 1))),
        ("ai_call_id", ai_call_id is None or (_inteiro(ai_call_id) and ai_call_id > 0)),
        ("estado_hash", estado_hash is None or (isinstance(estado_hash, str) and bool(_HASH.fullmatch(estado_hash)))),
    )
    v += [f"formato:{coluna}" for coluna, ok in regras if not ok]
    return v


#: O que a contagem mostra no lugar de um valor fora do vocabulário: o valor pode ser texto e nunca sai.
FORA_DO_VOCABULARIO = "(fora do vocabulário)"


def _vocab(valor: object, vocabulario: Sequence[str]) -> str:
    """O valor só aparece numa contagem se for do vocabulário fechado da coluna."""
    return str(valor) if valor in vocabulario else FORA_DO_VOCABULARIO


# ------------------------------------------------------------------ distribuições
def distribuicao(valores: Sequence[float]) -> dict[str, Any]:
    faixas = Counter()
    for x in valores:
        rotulo = f"< {FAIXAS[0]:.2f}"
        for inicio in FAIXAS:
            if x >= inicio:
                rotulo = f">= {inicio:.2f}"
        faixas[rotulo] += 1
    return {"n": len(valores), "min": round(min(valores), 4) if valores else None,
            "p50": percentil(valores, 50), "p95": percentil(valores, 95),
            "max": round(max(valores), 4) if valores else None,
            "acima_do_limiar": sum(1 for x in valores if x >= LIMIAR), "faixas": dict(sorted(faixas.items()))}


# ------------------------------------------------------------------ marca de POST (083, 31.21)
def ler_marcas(intencao: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], set[int], int]:
    """Por CHAMADA (a marca vai em todas as linhas dela): quantas chegaram ao POST, a `rede` antes/depois/sem marca e a
    régua cega. Devolve o resumo, os `ai_call_id` das chamadas postadas (para o cruzamento por id) e quantas chamadas
    têm linhas com marcas diferentes (defeito do registro)."""
    por_chamada: dict[str, set[tuple[object, object]]] = {}
    rede: set[str] = set()
    for x in intencao:
        d = dict(x)
        por_chamada.setdefault(str(d["chamada"]), set()).add((d.get("postado"), d.get("ai_call_id")))
        if d["fallback_reason"] == "rede":
            rede.add(str(d["chamada"]))
    divergentes = sum(1 for marcas in por_chamada.values() if len(marcas) > 1)
    marca = {c: next(iter(sorted(m, key=repr))) for c, m in por_chamada.items()}

    def _rotulo(postado: object) -> str:
        return str(postado) if postado in (0, 1) and _inteiro(postado) else SEM_MARCA

    ids = {int(i) for p, i in marca.values() if p == 1 and _inteiro(i) and int(i) > 0}
    resumo = {
        "chamadas_por_marca": dict(Counter(_rotulo(p) for p, _ in marca.values())),
        "rede": {"antes_do_post": sum(1 for c in rede if marca[c][0] == 0),
                 "depois_do_post": sum(1 for c in rede if marca[c][0] == 1),
                 SEM_MARCA: sum(1 for c in rede if _rotulo(marca[c][0]) == SEM_MARCA)},
        "regua_cega": sum(1 for p, i in marca.values() if p == 1 and i is None),
    }
    return resumo, ids, divergentes


# ------------------------------------------------------------------ montagem
def ler_sombra(linhas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    intencao = [x for x in linhas if x["origem"] == ORIGEM]
    violacoes: Counter[str] = Counter(v for x in linhas for v in violacoes_da_linha(x))
    por_pergunta: dict[str, dict[str, int]] = {}
    for pid, nome in PERGUNTAS.items():
        da = [x for x in intencao if x["pergunta_id"] == pid]
        por_pergunta[nome] = {"pedidos": len(da),
                              "respondidas": sum(1 for x in da if x["fallback_reason"] is None and x["escolha"]),
                              "abstencoes": sum(1 for x in da if x["fallback_reason"] is None
                                                and x["escolha"] == ID_NENHUMA),
                              "fallbacks": sum(1 for x in da if x["fallback_reason"] is not None)}
    confianca = [float(x["confianca"]) for x in intencao if isinstance(x["confianca"], (int, float))]
    p_escolha, maior, sem_p_escolha, respondida_abaixo = [], [], 0, 0
    for x in intencao:
        probs = _probabilidades(x["probabilidades"])
        if probs is None:                     # já contada como `formato:probabilidades`; não vira também defeito da porta
            continue
        if probs:
            maior.append(max(probs.values()))
        # escolha fora do formato já é `formato:escolha`; a porta só se confere com a escolha opaca
        if x["fallback_reason"] is None and _opaco(x["escolha"]):
            p = probs.get(str(x["escolha"]))
            if p is None:
                sem_p_escolha += 1
            else:
                p_escolha.append(p)
                respondida_abaixo += p < LIMIAR
    # A porta (31.19) só deixa passar resposta com a probabilidade da escolha no limiar: o contrário é defeito dela.
    if sem_p_escolha:
        violacoes["porta:respondida_sem_probabilidade_da_escolha"] += sem_p_escolha
    if respondida_abaixo:
        violacoes["porta:respondida_abaixo_do_limiar"] += respondida_abaixo
    chamadas_ms: dict[str, float] = {}
    for x in intencao:
        if float(x["ms"] or 0) > 0:
            chamadas_ms.setdefault(str(x["chamada"]), float(x["ms"]))
    marcas, ids_postados, divergentes = ler_marcas(intencao)
    if divergentes:
        violacoes["marca:chamada_com_marcas_diferentes"] += divergentes
    return {
        "_ids_postados": ids_postados,              # só para o cruzamento por id; `montar` tira antes de sair
        "linhas": len(linhas),
        "por_origem_classe_modo": dict(Counter(f"{_vocab(x['origem'], ORIGENS)}/{_vocab(x['classe'], CLASSES)}/"
                                               f"{_vocab(x['modo'], MODOS)}" for x in linhas)),
        "intencao": {
            "linhas": len(intencao), "chamadas": len({x["chamada"] for x in intencao}),
            "comandos": len({x["run_id"] for x in intencao if x["run_id"]}),
            "por_pergunta": por_pergunta,
            "fallbacks": dict(Counter(_vocab(x["fallback_reason"], FALLBACKS) for x in intencao
                                      if x["fallback_reason"])),
            "motivos_de_privacidade": dict(Counter(_vocab(x["motivo_privacidade"], MOTIVOS_DE_PRIVACIDADE)
                                                   if x["motivo_privacidade"] else "(sem motivo)" for x in intencao
                                                   if x["fallback_reason"] == "privacidade")),
            "decisao_real_casada": sum(1 for x in intencao if x["decisao_real"]),
            "ambiguos": dict(Counter(str(x["ambiguos"]) if isinstance(x["ambiguos"], int) else FORA_DO_VOCABULARIO
                                     for x in intencao if x["ambiguos"] is not None)),
            "confianca": distribuicao(confianca),
            "probabilidade_da_escolha": distribuicao(p_escolha),
            "maior_probabilidade": distribuicao(maior),
            "ms_por_chamada_na_sombra": {"n": len(chamadas_ms), "p50": percentil(list(chamadas_ms.values()), 50),
                                         "p95": percentil(list(chamadas_ms.values()), 95)},
            "usd_na_sombra": round(sum(float(x["usd"] or 0) for x in intencao), 6),
            "chamadas_postadas": len({x["chamada"] for x in intencao if x["fallback_reason"] not in SEM_POST}),
            "marca_de_post": marcas,
        },
        "limiar": LIMIAR,
        "violacoes": dict(violacoes),
        "nivel": "PROVED",
    }


def ler_gasto(chamadas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ok = [c for c in chamadas if c["ok"]]
    ms = [float(c["ms"] or 0) for c in ok]
    usd = sum(float(c["usd"] or 0) for c in chamadas)
    return {"chamadas": len(chamadas), "ok": len(ok),
            # `sombra.registrar` grava em `error_kind` só um motivo de FALLBACKS; outra coisa pode ser texto e não sai
            "falhas": dict(Counter(_vocab(c["error_kind"], FALLBACKS) if c["error_kind"] else "?"
                                   for c in chamadas if not c["ok"])),
            "usd": round(usd, 6), "usd_por_chamada": round(usd / len(chamadas), 6) if chamadas else None,
            "tokens_entrada": sum(int(c["input_tokens"] or 0) for c in chamadas),
            "tokens_saida": sum(int(c["output_tokens"] or 0) for c in chamadas),
            "ms_p50": percentil(ms, 50), "ms_p95": percentil(ms, 95), "ms_max": round(max(ms), 1) if ms else None,
            "modelos": sorted({str(c["model"]) if isinstance(c["model"], str) and _ID.fullmatch(c["model"])
                               else FORA_DO_VOCABULARIO for c in chamadas}), "nivel": "PROVED"}


def montar(conn: sqlite3.Connection, *, desde: str) -> dict[str, Any]:
    linhas = conn.execute("SELECT * FROM decisao_fechada_sombra WHERE ts >= ? ORDER BY ts, id", (desde,)).fetchall()
    chamadas = conn.execute("SELECT * FROM ai_calls WHERE ts >= ? AND provider=? AND origem=? AND ref LIKE ?"
                            " ORDER BY ts, id", (desde, PROVEDOR_DO_JEV, ORIGEM_NO_GASTO, f"{ORIGEM}:%")).fetchall()
    sombra = ler_sombra(linhas)
    gasto = ler_gasto(chamadas)
    postadas = sombra["intencao"]["chamadas_postadas"]
    por_id = cruzar_por_id(conn, sombra.pop("_ids_postados"), {int(c["id"]) for c in chamadas})
    problemas = sorted(sombra["violacoes"]) + (["cruzamento:ai_call_id_sem_linha_do_jev"] if por_id["nao_achadas"] else [])
    # falha fechado: com violação em qualquer linha (de qualquer origem), nem a falta de amostra da intenção passa
    veredito = "FALHOU" if problemas else ("SEM AMOSTRA" if not sombra["intencao"]["linhas"] else "OK")
    return {"desde": desde, "sombra": sombra, "gasto": gasto,
            "cruzamento": {"chamadas_postadas_na_sombra": postadas, "linhas_em_ai_calls": gasto["chamadas"],
                           "bate": postadas == gasto["chamadas"]},
            "cruzamento_por_id": por_id, "problemas": problemas, "veredito": veredito}


def cruzar_por_id(conn: sqlite3.Connection, ids_postados: set[int], ids_no_periodo: set[int]) -> dict[str, Any]:
    """O cruzamento EXATO da 083: cada `ai_call_id` de chamada postada tem de ser uma linha do Jev da intenção em
    `ai_calls`. Procura por id, e não pela janela `ts >= desde`, porque a linha de gasto nasce antes da linha da sombra
    e uma chamada na borda do `--desde` não pode reprovar. A linha de `ai_calls` sem linha marcada na sombra só informa:
    as anteriores à 083, a exceção depois do POST e o futuro em voo no `on` não têm marca."""
    achadas: set[int] = set()
    lista = sorted(ids_postados)
    for i in range(0, len(lista), 500):
        lote = lista[i:i + 500]
        achadas |= {int(r["id"]) for r in conn.execute(
            f"SELECT id FROM ai_calls WHERE id IN ({','.join('?' * len(lote))}) AND provider=? AND origem=? AND ref LIKE ?",
            (*lote, PROVEDOR_DO_JEV, ORIGEM_NO_GASTO, f"{ORIGEM}:%"))}
    return {"chamadas_postadas_com_id": len(lista), "achadas": len(achadas), "nao_achadas": len(lista) - len(achadas),
            "ai_calls_sem_linha_marcada": len(ids_no_periodo - ids_postados), "bate": len(achadas) == len(lista),
            "nivel": "PROVED"}


def resumo(rel: Mapping[str, Any]) -> str:
    s, i, g, x = rel["sombra"], rel["sombra"]["intencao"], rel["gasto"], rel["cruzamento"]
    r2, r3 = i["por_pergunta"]["R2"], i["por_pergunta"]["R3"]
    m, p = i["marca_de_post"], rel["cruzamento_por_id"]
    problemas = {**s["violacoes"], **({"cruzamento:ai_call_id_sem_linha_do_jev": p["nao_achadas"]} if p["nao_achadas"]
                                      else {})}

    def _dist(d: Mapping[str, Any]) -> str:
        return (f"n {d['n']}, p50 {d['p50']}, p95 {d['p95']}, máx. {d['max']}, ≥ limiar {d['acima_do_limiar']}"
                if d["n"] else "sem valores")

    return "\n".join([
        f"1. Sombra de intenção desde {rel['desde']}: {rel['veredito']} (limiar {s['limiar']})",
        f"2. Linhas (PROVED): {s['linhas']} no total, por origem/classe/modo {s['por_origem_classe_modo'] or '—'}",
        f"3. Intenção: {i['linhas']} linhas, {i['chamadas']} chamadas, {i['comandos']} comandos",
        f"4. R2: {r2['pedidos']} pedidos, {r2['respondidas']} respondidas ({r2['abstencoes']} nenhuma), "
        f"{r2['fallbacks']} fallbacks; R3: {r3['pedidos']} pedidos, {r3['respondidas']} respondidas, {r3['fallbacks']} fallbacks",
        f"5. Recusas por fallback: {i['fallbacks'] or 'nenhuma'}; privacidade por motivo: {i['motivos_de_privacidade'] or '—'}; "
        f"rede por chamada (083): {m['rede']['antes_do_post']} antes do POST, {m['rede']['depois_do_post']} depois, "
        f"{m['rede'][SEM_MARCA]} sem marca",
        f"6. Confiança: {_dist(i['confianca'])}",
        f"7. Probabilidade da escolha (respondidas): {_dist(i['probabilidade_da_escolha'])}; "
        f"maior probabilidade (todas): {_dist(i['maior_probabilidade'])}",
        f"8. Custo (ai_calls, ref intencao:*): {g['chamadas']} chamadas ({g['ok']} ok; falhas {g['falhas'] or 'nenhuma'}), "
        f"US$ {g['usd']} (por chamada {g['usd_por_chamada']}; na sombra {i['usd_na_sombra']}), "
        f"tokens {g['tokens_entrada']} + {g['tokens_saida']}",
        f"9. Latência: p50 {g['ms_p50']} ms, p95 {g['ms_p95']} ms, máx. {g['ms_max']} ms; cruzamento "
        f"{x['chamadas_postadas_na_sombra']} postadas × {x['linhas_em_ai_calls']} em ai_calls "
        f"({'bate' if x['bate'] else 'NÃO bate'}; informa); por id (083): {p['achadas']} de "
        f"{p['chamadas_postadas_com_id']} achadas ({'bate' if p['bate'] else 'NÃO bate'}), régua cega {m['regua_cega']}, "
        f"chamadas por marca {m['chamadas_por_marca'] or '—'}",
        f"10. Zero texto e marca: {'nenhuma violação' if not rel['problemas'] else 'VIOLAÇÕES ' + str(problemas)}",
    ])


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--db", default=str(RAIZ / "data" / "poc.sqlite3"))
    p.add_argument("--desde", default=T_ON, help=f"ISO-8601 UTC; padrão: a partida do deploy 11 ({T_ON})")
    p.add_argument("--json", help="arquivo do JSON completo (só contagens, motivos e números)")
    args = p.parse_args(argv)
    conn = abrir(Path(args.db))
    try:
        rel = montar(conn, desde=_desde(args.desde))
    finally:
        conn.close()
    print(resumo(rel))
    if args.json:
        Path(args.json).write_text(json.dumps(rel, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return {"OK": 0, "FALHOU": 1}.get(rel["veredito"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
