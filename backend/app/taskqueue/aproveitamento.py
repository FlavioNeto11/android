"""Aproveitamento das receitas: quanto trabalho de IA elas já pouparam e, onde não pouparam, por quê.

Leitura pura das tabelas que o executor já grava (`steps`, `recipes`, `ai_calls`, `runs`, `flows`) — nenhuma
chamada de modelo, nenhum aparelho. Responde, por fluxo e app, o que a cobertura sozinha não diz: das etapas que
PODIAM usar receita, quantas usaram, quantas voltaram para a IA, e quantas chamadas de IA isso evitou.

O que os dados permitem separar, e o que não:
- **Versão do app**: `steps` não guarda a versão do aparelho; só `recipes` guarda. Por isso o recorte por versão vem
  dos contadores da própria receita (acumulados desde que ela existe, fora da janela), e o recorte por fluxo/app vem
  das etapas e chamadas DA JANELA.
- **Sem cobertura × receita de outra chave**: uma etapa conduzida só pela IA é "sem cobertura" quando nenhuma receita
  daquele passo existia antes dela começar; se existia (de outra versão, variante, assinatura, ou já em quarentena),
  é "outra chave ou quarentena" — as quatro causas não se separam sem a versão na etapa.
- **Defeito no aparelho × receita que envelheceu**: uma divergência (`recipe+ai`) num aparelho, com a MESMA etapa
  reproduzida limpa noutro aparelho da mesma execução, é do aparelho (estado, aviso, tela própria); divergência em
  todos os que tentaram é da receita/tela. Aparelho sozinho na execução fica "indeterminado".
- **Nova tentativa**: a etapa que diverge e termina em `retry` não recebe `driven_by` (o executor não dá veredito
  sobre a receita nesse caso) e aparece como `sem_registro`; o retorno à IA dela só existe na métrica
  `receita.retorno_ia` do processo.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Any

from ..db import Database, loads
from ..util import now, to_iso

#: Janela padrão, a mesma do custo mediano por etapa (`social/capacidades.py`, item 7.7).
JANELA_DIAS = 7


def _vazio() -> dict[str, Any]:
    return {"etapas": 0, "elegiveis": 0, "por_receita": 0, "receita_mais_ia": 0, "so_ia": 0, "sem_registro": 0,
            "sem_cobertura": 0, "outra_chave_ou_quarentena": 0, "receitas_aprendidas": 0,
            "divergencias": {"so_neste_aparelho": 0, "em_todos_os_aparelhos": 0, "indeterminado": 0},
            "chamadas_ia": {"decide": 0, "verify": 0},
            "chamadas_evitadas_estimadas": 0.0, "etapas_por_receita_sem_base": 0}


def aproveitamento(db: Database, *, dias: int = JANELA_DIAS, agora: datetime | None = None) -> dict[str, Any]:
    """Resumo por (fluxo, app) das etapas iniciadas nos últimos `dias` dias, mais o recorte por versão das receitas.

    `chamadas_evitadas_estimadas`: para cada etapa reproduzida só por receita, a mediana de decisões de IA (`decide`)
    que a MESMA etapa (pacote + `template_hash`) custou quando a IA a conduziu sozinha e deu certo; sem esse histórico,
    a mediana do app; sem nenhum, a etapa entra em `etapas_por_receita_sem_base` e não soma nada — estimativa sem base
    não vira número. Verificação não entra: a pós-condição é conferida com ou sem receita.
    """
    desde = to_iso((agora or now()) - timedelta(days=dias))
    etapas = db.query(
        "SELECT id, run_id, instance_id, template_hash, driven_by, status, app_id, started_at FROM steps"
        " WHERE started_at IS NOT NULL AND started_at >= ? AND for_each IS NULL", (desde,))

    # ---------- fluxo e pacote de cada execução (uma leitura por execução, não por etapa)
    run_ids = sorted({e["run_id"] for e in etapas})
    fluxo_de_origem = {r["source_run_id"]: r["id"] for r in db.query(
        "SELECT id, source_run_id FROM flows WHERE source_run_id IS NOT NULL")}
    pacote_do_app = {r["id"]: r["package"] for r in db.query("SELECT id, package FROM apps")}
    execucoes: dict[str, tuple[str | None, str | None]] = {}
    for i in range(0, len(run_ids), 500):                  # uma consulta por lote, não uma por execução
        lote = run_ids[i:i + 500]
        for run in db.query(f"SELECT id, flow_id, plan FROM runs WHERE id IN ({','.join('?' for _ in lote)})",
                            tuple(lote)):
            plano = loads(run["plan"], {}) or {}
            pacote = pacote_do_app.get(plano.get("app_id")) or plano.get("app_package")
            execucoes[run["id"]] = (run["flow_id"] or fluxo_de_origem.get(run["id"]), pacote)

    def pacote_da_etapa(e: Any) -> str | None:
        return pacote_do_app.get(e["app_id"]) if e["app_id"] else execucoes.get(e["run_id"], (None, None))[1]

    # ---------- chamadas de IA por etapa (só as das etapas da janela)
    chamadas: dict[str, dict[str, int]] = {}
    for c in db.query("SELECT step_id, role, COUNT(*) AS n FROM ai_calls WHERE ts >= ? AND step_id IS NOT NULL"
                      " AND role IN ('decide','verify') GROUP BY step_id, role", (desde,)):
        chamadas.setdefault(c["step_id"], {})[c["role"]] = int(c["n"])

    # ---------- base da estimativa: decisões por etapa conduzida só pela IA que deu certo
    base_etapa: dict[tuple[str | None, str | None], list[int]] = {}
    base_app: dict[str | None, list[int]] = {}
    for e in etapas:
        if e["driven_by"] == "ai" and e["status"] == "succeeded":
            n = chamadas.get(e["id"], {}).get("decide", 0)
            pacote = pacote_da_etapa(e)
            base_etapa.setdefault((pacote, e["template_hash"]), []).append(n)
            base_app.setdefault(pacote, []).append(n)

    # ---------- receita existente antes de cada etapa começar (qualquer versão/estado) e aprendidas por etapa
    primeira_receita = {(r["app_package"], r["step_hash"]): r["primeira"] for r in db.query(
        "SELECT app_package, step_hash, MIN(created_at) AS primeira FROM recipes GROUP BY app_package, step_hash")}
    ids_janela = {e["id"] for e in etapas}
    aprendidas_por_etapa = {r["learned_from_step"] for r in db.query(
        "SELECT learned_from_step FROM recipes WHERE learned_from_step IS NOT NULL AND created_at >= ?", (desde,))
        if r["learned_from_step"] in ids_janela}

    # ---------- divergências: irmãs da mesma etapa na mesma execução
    uso_de_receita: dict[tuple[str, str | None], dict[str, int]] = {}
    for e in etapas:
        if e["driven_by"] in ("recipe", "recipe+ai"):
            uso = uso_de_receita.setdefault((e["run_id"], e["template_hash"]), {"recipe": 0, "recipe+ai": 0})
            uso[e["driven_by"]] += 1

    grupos: dict[tuple[str | None, str | None], dict[str, Any]] = {}
    hashes_do_grupo: dict[tuple[str | None, str | None], set[str]] = {}
    for e in etapas:
        fluxo = execucoes.get(e["run_id"], (None, None))[0]
        pacote = pacote_da_etapa(e)
        g = grupos.setdefault((fluxo, pacote), _vazio())
        g["etapas"] += 1
        origem = e["driven_by"]
        chaves = {"recipe": "por_receita", "recipe+ai": "receita_mais_ia", "ai": "so_ia"}
        g[chaves.get(origem, "sem_registro")] += 1
        n = chamadas.get(e["id"], {})
        g["chamadas_ia"]["decide"] += n.get("decide", 0)
        g["chamadas_ia"]["verify"] += n.get("verify", 0)
        elegivel = bool(origem and pacote and e["template_hash"])
        if not elegivel:
            continue
        g["elegiveis"] += 1
        hashes_do_grupo.setdefault((fluxo, pacote), set()).add(e["template_hash"])
        if origem == "ai":
            primeira = primeira_receita.get((pacote, e["template_hash"]))
            if primeira is None or primeira >= e["started_at"]:
                g["sem_cobertura"] += 1
            else:
                g["outra_chave_ou_quarentena"] += 1
            if e["id"] in aprendidas_por_etapa:
                g["receitas_aprendidas"] += 1
        elif origem == "recipe+ai":
            uso = uso_de_receita.get((e["run_id"], e["template_hash"]), {"recipe": 0, "recipe+ai": 0})
            if uso["recipe"]:                              # a mesma etapa, na mesma execução, foi limpa noutro aparelho
                g["divergencias"]["so_neste_aparelho"] += 1
            elif uso["recipe+ai"] >= 2:
                g["divergencias"]["em_todos_os_aparelhos"] += 1
            else:
                g["divergencias"]["indeterminado"] += 1
        elif origem == "recipe":
            amostra = base_etapa.get((pacote, e["template_hash"])) or base_app.get(pacote)
            if amostra:
                g["chamadas_evitadas_estimadas"] += float(median(amostra))
            else:
                g["etapas_por_receita_sem_base"] += 1

    # ---------- recorte por versão (contadores da receita, acumulados)
    saida = []
    for (fluxo, pacote), g in sorted(grupos.items(), key=lambda kv: (-kv[1]["etapas"], str(kv[0]))):
        g["chamadas_evitadas_estimadas"] = round(g["chamadas_evitadas_estimadas"], 1)
        g["retorno_ia"] = g["receita_mais_ia"]
        g["por_versao"] = _por_versao(db, pacote, hashes_do_grupo.get((fluxo, pacote), set()))
        saida.append({"flow_id": fluxo, "package": pacote, **g})

    totais = _vazio()
    for g in saida:
        for k, v in g.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and k in totais:
                totais[k] += v
            elif isinstance(v, dict) and k in totais:
                for kk, vv in v.items():
                    totais[k][kk] += vv
    totais["chamadas_evitadas_estimadas"] = round(totais["chamadas_evitadas_estimadas"], 1)
    totais["retorno_ia"] = totais["receita_mais_ia"]
    return {"janela_dias": dias, "desde": desde, "fluxos": saida, "totais": totais}


def _por_versao(db: Database, pacote: str | None, hashes: set[str]) -> list[dict[str, Any]]:
    """Receitas das etapas deste grupo, por versão do app: quantas ativas e em quarentena, e quantas reproduções
    deram certo ou divergiram desde que cada receita existe (contadores da tabela, não da janela)."""
    if not pacote or not hashes:
        return []
    marcas = ",".join("?" for _ in hashes)
    return [{"app_version": r["app_version"], "receitas_ativas": int(r["ativas"] or 0),
             "em_quarentena": int(r["quarentena"] or 0), "reproducoes_ok": int(r["ok"] or 0),
             "reproducoes_divergidas": int(r["falhas"] or 0)}
            for r in db.query(
                "SELECT app_version, SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS ativas,"
                " SUM(CASE WHEN status='quarantined' THEN 1 ELSE 0 END) AS quarentena,"
                " SUM(replay_ok) AS ok, SUM(replay_fail) AS falhas FROM recipes"
                f" WHERE app_package=? AND step_hash IN ({marcas}) GROUP BY app_version ORDER BY app_version",
                (pacote, *sorted(hashes)))]
