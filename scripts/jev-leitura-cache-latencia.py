"""Cache, entrada e latência das chamadas de IA, ANTES × DEPOIS de um corte (o deploy), por função e por perfil.

Só leitura e sem IA: lê `ai_calls` (003, 032, 048, 073 e, quando existe, as quatro colunas do RA-10, migração 080) e
`runs.ai_profile` (064). O banco abre em modo só leitura em TODA conexão (`PRAGMA query_only` no SQLite, `READ ONLY` no
PostgreSQL). É a régua para medir o ganho do RA-17 e do caminho rápido nas execuções reais depois de um deploy, sem A/B
pago.

Uso, a partir da raiz do checkout:

    backend/.venv/Scripts/python.exe scripts/jev-leitura-cache-latencia.py --corte 2026-10-04T12:00:00Z
        [--desde <ISO>] [--ate <ISO>] [--db data/poc.sqlite3 | --dsn postgresql://...] [--origem execucao]
        [--por-motivo] [--minimo 20] [--json saida.json] [--md saida.md]

O corte é o INSTANTE DO DEPLOY em ISO-8601 UTC (o mesmo `:deploy` da conferência do RA-10 em `docs/ia.md`). Um hash de
commit também é aceito, mas vale a data do COMMIT, não a do deploy: as chamadas entre o commit e o deploy caem no
"depois". Nenhuma tabela liga commit a instante de deploy, e o script não inventa um.

Definições (as de `repository.add_usage`):
- `input_tokens` é a entrada FRESCA (sem o que veio do cache). Entrada total = fresca + cache lido + cache escrito.
- Função = `ai_calls.role`. O ator grava `decide`; a chamada escalonada (`tier` ≥ 1) vai para `escalation`, com a
  função de origem em `escalation_de`.
- Perfil = `runs.ai_profile`: NULO é `padrão`, e a chamada sem execução é `fora de execução`.
- Latência só nas chamadas `ok=1` (o erro inclui o prazo estourado); os erros contam à parte.
- Custo: a regra de `planning/costs.py`. O `usd` DECLARADO (imagem, Jev) vale; senão, tokens × `ai.prices` do modelo
  que respondeu. O `ai.prices` vem do `config/config.yaml` desta instalação (`--config`; o YAML substitui a tabela
  inteira, como no pydantic), ou do padrão do código. Só essa chave é lida: o `.env` não é aberto. O modelo sem preço
  paga a tarifa mais cara, e o custo do grupo fica `estimado`. As linhas do provedor `simulated` ficam fora, como no
  relatório de custo.

Níveis: as medidas são contagens e percentis do banco (`PROVED`). Atribuir a diferença ao RA-17 ou ao caminho rápido é
`INFERRED`: antes × depois não é A/B, porque o tráfego muda entre as janelas.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

import yaml  # noqa: E402

from app.config import AiCfg  # noqa: E402
from app.db import Database  # noqa: E402
from app.planning.costs import COLUNAS, effective_price  # noqa: E402

#: As quatro colunas do RA-10 (migração 080). Sem elas, a quebra por motivo é `not_run`.
COLUNAS_RA10 = ("verdict", "escalate", "motivo", "image_reason")
#: As diferenças que o relatório calcula (depois − antes), quando os dois lados têm valor.
DELTAS = ("cache_lido", "cache_escrito", "chamadas_com_cache_lido", "entrada_total_p50", "entrada_fresca_p50",
          "ms_p50", "ms_p95", "usd_por_etapa_p50", "usd_por_etapa_media", "ms_por_etapa_p50", "usd_por_execucao_p50")
TODAS, TODOS = "(todas)", "(todos)"
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


# ------------------------------------------------------------------ tempo e corte
def _iso(dt: datetime) -> str:
    """O formato de `ai_calls.ts` (`now_iso`: milissegundos e Z). A comparação é de TEXTO: "…:00Z" > "…:00.123Z"."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.astimezone(UTC).microsecond // 1000:03d}Z"


def _data(texto: str) -> datetime:
    dt = datetime.fromisoformat(texto.strip().replace("Z", "+00:00"))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def resolver_corte(valor: str, raiz: Path = RAIZ) -> tuple[datetime, str]:
    """(instante, fonte). ISO-8601 é o instante do deploy; um hash vira a data do COMMIT, com o aviso na fonte."""
    if _COMMIT.match(valor.strip().lower()):
        saida = subprocess.run(["git", "-C", str(raiz), "show", "-s", "--format=%cI", valor.strip()],
                               capture_output=True, text=True, check=False)
        if saida.returncode != 0 or not saida.stdout.strip():
            raise SystemExit(f"commit não encontrado: {valor}")
        return (_data(saida.stdout.strip()),
                f"data do commit {valor.strip()}, NÃO do deploy: as chamadas entre o commit e o deploy caem no 'depois'")
    try:
        return _data(valor), "instante informado (ISO-8601 UTC)"
    except ValueError as exc:
        raise SystemExit(f"corte inválido (ISO-8601 UTC ou hash de commit): {valor}") from exc


def janelas(corte: datetime, desde: datetime | None, ate: datetime | None,
            agora: datetime) -> tuple[datetime, datetime, datetime]:
    """(desde, corte, até). Sem `--desde`, o "antes" tem a MESMA duração do "depois": comparar um dia contra duas
    semanas misturaria o tráfego de épocas diferentes."""
    fim = ate or agora
    if fim <= corte:
        raise SystemExit("a janela do 'depois' está vazia: --ate (ou agora) precisa ser posterior ao corte")
    inicio = desde or (corte - (fim - corte))
    if inicio >= corte:
        raise SystemExit("--desde precisa ser anterior ao corte")
    return inicio, corte, fim


# ------------------------------------------------------------------ preços
def precos(caminho: Path | None) -> tuple[dict[str, list[float]], str]:
    """(tabela, fonte). `ai.prices` do YAML desta instalação; sem ele, o padrão do código (`AiCfg.prices`)."""
    if caminho is not None and caminho.is_file():
        dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
        tabela = (dados.get("ai") or {}).get("prices") if isinstance(dados, dict) else None
        if isinstance(tabela, dict) and tabela:
            return {str(k): [float(x) for x in v] for k, v in tabela.items()}, f"{caminho.name} (ai.prices)"
    return {k: list(v) for k, v in AiCfg().prices.items()}, "padrão do código (AiCfg.prices)"


def custo_da_linha(linha: Mapping[str, Any], tabela: Mapping[str, list[float]]) -> tuple[float, bool]:
    """(US$, estimado?). O `usd` declarado vence os tokens, como em `costs.spent_usd`."""
    if linha["usd"] is not None:
        return float(linha["usd"]), False
    preco, estimado = effective_price(dict(tabela), str(linha["model"] or ""))
    return sum(float(linha[c] or 0) * preco[i] for i, c in enumerate(COLUNAS)) / 1_000_000, estimado


# ------------------------------------------------------------------ leitura
def colunas_de_ai_calls(db: Any) -> set[str]:
    if db.dialect == "postgres":
        linhas = db.query("SELECT column_name AS name FROM information_schema.columns WHERE table_name='ai_calls'")
    else:
        linhas = db.query("PRAGMA table_info(ai_calls)")
    return {str(linha["name"]) for linha in linhas}


def ler_chamadas(db: Any, desde: datetime, ate: datetime, *, origem: str | None, ra10: bool,
                 tabela: Mapping[str, list[float]]) -> list[dict[str, Any]]:
    extras = "".join(f", c.{c}" for c in COLUNAS_RA10) if ra10 else ""
    sql = ("SELECT c.ts, c.run_id, c.step_id, c.role, c.model, c.tier, c.input_tokens, c.cache_read, c.cache_write,"
           " c.output_tokens, c.with_image, c.ms, c.ok, c.usd, c.provider, c.origem, r.ai_profile" + extras +
           " FROM ai_calls c LEFT JOIN runs r ON r.id = c.run_id WHERE c.ts >= ? AND c.ts < ?")
    params: list[Any] = [_iso(desde), _iso(ate)]
    if origem:
        sql += " AND c.origem = ?"
        params.append(origem)
    linhas = [dict(linha) for linha in db.query(sql + " ORDER BY c.ts, c.id", tuple(params))]
    for linha in linhas:
        linha["_usd"], linha["_estimado"] = custo_da_linha(linha, tabela)
    return linhas


def funcao(linha: Mapping[str, Any]) -> str:
    return "escalation" if int(linha["tier"] or 0) >= 1 else str(linha["role"])


def perfil(linha: Mapping[str, Any]) -> str:
    if not linha["run_id"]:
        return "fora de execução"
    return str(linha["ai_profile"]) if linha["ai_profile"] else "padrão"


# ------------------------------------------------------------------ medidas
def _taxa(parte: float, todo: float) -> float | None:
    return round(parte / todo, 4) if todo else None


def _pct(valores: Sequence[float], q: float) -> float | None:
    """Percentil pelo posto mais próximo: um valor que existiu, sem interpolar."""
    if not valores:
        return None
    ordenados = sorted(valores)
    return ordenados[max(0, math.ceil(q * len(ordenados)) - 1)]


def _entrada_total(linha: Mapping[str, Any]) -> int:
    return int(linha["input_tokens"] or 0) + int(linha["cache_read"] or 0) + int(linha["cache_write"] or 0)


def _ra10(linhas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    carimbadas = [l for l in linhas if any(l.get(c) is not None for c in COLUNAS_RA10)]

    def _conta(coluna: str) -> dict[str, int]:
        return dict(Counter(str(l[coluna]) for l in carimbadas if l.get(coluna) is not None).most_common())

    # A discordância do rejulgamento, como o cabeçalho da 080 a define: o modelo forte desfez o veredito do barato.
    discordancias = sum(1 for l in carimbadas
                        if (l.get("escalate") == "nivel" and l.get("verdict") == "yes")
                        or (l.get("escalate") == "sim_com_efeito" and l.get("verdict") not in (None, "yes")))
    return {"nivel": "PROVED", "sem_carimbo": len(linhas) - len(carimbadas), "motivos": _conta("motivo"),
            "escalates": _conta("escalate"), "image_reasons": _conta("image_reason"),
            "discordancias_do_rejulgamento": discordancias}


def medir(linhas: Sequence[Mapping[str, Any]], *, ra10: bool) -> dict[str, Any]:
    ok = [l for l in linhas if l["ok"]]
    totais = [_entrada_total(l) for l in linhas]
    soma = sum(totais)
    por_etapa: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for linha in linhas:
        if linha["step_id"]:
            por_etapa[str(linha["step_id"])].append(linha)
    usd_etapa = [sum(l["_usd"] for l in ls) for ls in por_etapa.values()]
    por_execucao: dict[str, float] = defaultdict(float)
    for linha in linhas:
        if linha["run_id"]:
            por_execucao[str(linha["run_id"])] += linha["_usd"]
    usd_execucao = list(por_execucao.values())
    ms_etapa = [sum(int(l["ms"] or 0) for l in ls if l["ok"]) for ls in por_etapa.values()]
    return {
        "chamadas": len(linhas), "erros": len(linhas) - len(ok),
        "cache_lido": _taxa(sum(int(l["cache_read"] or 0) for l in linhas), soma),
        "cache_escrito": _taxa(sum(int(l["cache_write"] or 0) for l in linhas), soma),
        "chamadas_com_cache_lido": _taxa(sum(1 for l in linhas if int(l["cache_read"] or 0) > 0), len(linhas)),
        "entrada_total_p50": _pct(totais, 0.5),
        "entrada_fresca_p50": _pct([int(l["input_tokens"] or 0) for l in linhas], 0.5),
        "saida_p50": _pct([int(l["output_tokens"] or 0) for l in linhas], 0.5),
        "com_imagem": _taxa(sum(1 for l in linhas if l["with_image"]), len(linhas)),
        "ms_p50": _pct([int(l["ms"] or 0) for l in ok], 0.5), "ms_p95": _pct([int(l["ms"] or 0) for l in ok], 0.95),
        "usd": round(sum(l["_usd"] for l in linhas), 6), "sem_preco": sum(1 for l in linhas if l["_estimado"]),
        "custo": "estimado" if any(l["_estimado"] for l in linhas) else "completo",
        "etapas": len(por_etapa), "fora_de_etapa": sum(1 for l in linhas if not l["step_id"]),
        "usd_por_etapa_p50": round(_pct(usd_etapa, 0.5), 6) if usd_etapa else None,
        "usd_por_etapa_media": round(sum(usd_etapa) / len(usd_etapa), 6) if usd_etapa else None,
        "ms_por_etapa_p50": _pct(ms_etapa, 0.5),
        # O planejador grava a chamada sem `step_id` (o plano é da execução): só o custo POR EXECUÇÃO enxerga o plano
        # que o caminho rápido deixou de pedir.
        "execucoes": len(por_execucao),
        "usd_por_execucao_p50": round(_pct(usd_execucao, 0.5), 6) if usd_execucao else None,
        "ra10": _ra10(linhas) if ra10 else {"nivel": "not_run", "motivo": "migração 080 ausente neste banco"},
    }


def _grupos(linhas: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], list[Mapping[str, Any]]]:
    """Por (função, perfil), mais o total de cada perfil e o total geral: o custo POR ETAPA só fecha somando as funções."""
    grupos: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for linha in linhas:
        f, p = funcao(linha), perfil(linha)
        grupos[(f, p)].append(linha)
        grupos[(TODAS, p)].append(linha)
        grupos[(TODAS, TODOS)].append(linha)
    return grupos


def comparar(antes: Sequence[Mapping[str, Any]], depois: Sequence[Mapping[str, Any]], *, ra10: bool,
             minimo: int, por_motivo: bool) -> list[dict[str, Any]]:
    ga, gd = _grupos(antes), _grupos(depois)
    saida = []
    for chave in sorted(set(ga) | set(gd), key=lambda k: (k[0] == TODAS, k[0], k[1] == TODOS, k[1])):
        a, d = medir(ga.get(chave, []), ra10=ra10), medir(gd.get(chave, []), ra10=ra10)
        item: dict[str, Any] = {
            "funcao": chave[0], "perfil": chave[1], "antes": a, "depois": d,
            "delta": {k: (round(d[k] - a[k], 6) if a[k] is not None and d[k] is not None else None) for k in DELTAS},
            "amostra": "pequena" if min(a["chamadas"], d["chamadas"]) < minimo else "ok",
        }
        if chave[0] == "escalation":
            item["escalation_de"] = {"antes": dict(Counter(str(l["role"]) for l in ga.get(chave, []))),
                                     "depois": dict(Counter(str(l["role"]) for l in gd.get(chave, [])))}
        if por_motivo and ra10:
            # O "antes" de um deploy que trouxe a 080 é todo `sem_carimbo`: a quebra compara de verdade só entre dois
            # deploys que já carimbam.
            motivos: dict[str, dict[str, Any]] = {}
            for lado, linhas in (("antes", ga.get(chave, [])), ("depois", gd.get(chave, []))):
                por: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
                for linha in linhas:
                    por[str(linha["motivo"]) if linha.get("motivo") is not None else "sem_carimbo"].append(linha)
                for motivo, ls in por.items():
                    motivos.setdefault(motivo, {})[lado] = {k: v for k, v in medir(ls, ra10=False).items() if k != "ra10"}
            item["por_motivo"] = dict(sorted(motivos.items()))
        saida.append(item)
    return saida


# ------------------------------------------------------------------ montagem e saída
def montar(db: Any, *, corte: datetime, fonte: str, desde: datetime | None = None, ate: datetime | None = None,
           agora: datetime, origem: str | None = None, minimo: int = 20, por_motivo: bool = False,
           tabela: Mapping[str, list[float]] | None = None, fonte_dos_precos: str | None = None) -> dict[str, Any]:
    inicio, corte, fim = janelas(corte, desde, ate, agora)
    ra10 = set(COLUNAS_RA10) <= colunas_de_ai_calls(db)
    if tabela is None:
        tabela, fonte_dos_precos = precos(None)
    linhas = ler_chamadas(db, inicio, fim, origem=origem, ra10=ra10, tabela=tabela)
    simuladas = [l for l in linhas if l["provider"] == "simulated"]
    reais = [l for l in linhas if l["provider"] != "simulated"]
    antes = [l for l in reais if str(l["ts"]) < _iso(corte)]
    depois = [l for l in reais if str(l["ts"]) >= _iso(corte)]
    return {
        "gerado_em": _iso(agora), "corte": _iso(corte), "corte_fonte": fonte,
        "janelas": {"antes": [_iso(inicio), _iso(corte)], "depois": [_iso(corte), _iso(fim)]},
        "origem": origem or "todas", "ra10": "presente" if ra10 else "ausente (migração 080)",
        "precos": fonte_dos_precos,
        "chamadas": {"antes": len(antes), "depois": len(depois), "simuladas_excluidas": len(simuladas)},
        "definicoes": {
            "funcao": "ai_calls.role (o ator grava decide); tier >= 1 vai para escalation",
            "perfil": "runs.ai_profile; NULO = padrão; sem run_id = fora de execução",
            "entrada_total": "input_tokens (fresca) + cache_read + cache_write",
            "cache_lido": "soma de cache_read / soma da entrada total",
            "latencia": "ms das chamadas ok=1, percentil pelo posto mais próximo",
            "custo": "usd declarado, senão tokens x ai.prices do modelo (planning/costs.py); sem preço = tarifa mais cara",
            "custo_por_etapa": "soma do custo das chamadas de cada step_id (o plano não tem step_id)",
            "custo_por_execucao": "soma do custo das chamadas de cada run_id (inclui o plano)",
        },
        "niveis": {"medidas": "PROVED (contagens e percentis do banco)",
                   "causa": "INFERRED: antes x depois não é A/B; o tráfego muda entre as janelas"},
        "grupos": comparar(antes, depois, ra10=ra10, minimo=minimo, por_motivo=por_motivo),
    }


def _fmt(valor: Any) -> str:
    if valor is None:
        return "–"
    if isinstance(valor, float):
        return f"{valor:.4g}"
    return str(valor)


def em_markdown(rel: Mapping[str, Any]) -> str:
    j = rel["janelas"]
    linhas = [f"# Cache e latência antes × depois — corte {rel['corte']}", "",
              f"Corte: {rel['corte_fonte']}. Antes: {j['antes'][0]} a {j['antes'][1]}. Depois: {j['depois'][0]} a "
              f"{j['depois'][1]}. Origem: {rel['origem']}. RA-10: {rel['ra10']}.",
              f"Chamadas: {rel['chamadas']['antes']} antes, {rel['chamadas']['depois']} depois "
              f"({rel['chamadas']['simuladas_excluidas']} simuladas fora).",
              f"Preços: {rel['precos']}. Níveis: {rel['niveis']['medidas']}; causa {rel['niveis']['causa']}.", "",
              "| Função | Perfil | Chamadas | Cache lido | Entrada total p50 | ms p50 | ms p95 | US$ por etapa p50 |"
              " US$ por execução p50 | Amostra |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for g in rel["grupos"]:
        a, d = g["antes"], g["depois"]

        def par(k: str, a: Mapping[str, Any] = a, d: Mapping[str, Any] = d) -> str:
            return f"{_fmt(a[k])} → {_fmt(d[k])}"

        linhas.append(f"| {g['funcao']} | {g['perfil']} | {par('chamadas')} | {par('cache_lido')} | "
                      f"{par('entrada_total_p50')} | {par('ms_p50')} | {par('ms_p95')} | {par('usd_por_etapa_p50')} | "
                      f"{par('usd_por_execucao_p50')} | {g['amostra']} |")
    return "\n".join(linhas) + "\n"


# ------------------------------------------------------------------ banco e CLI
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


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--corte", required=True, help="instante do deploy (ISO-8601 UTC) ou hash de commit (data do commit)")
    p.add_argument("--desde", help="início do 'antes' (ISO-8601 UTC); padrão: a mesma duração do 'depois'")
    p.add_argument("--ate", help="fim do 'depois' (ISO-8601 UTC); padrão: agora")
    p.add_argument("--db", default=str(RAIZ / "data" / "poc.sqlite3"))
    p.add_argument("--config", default=str(RAIZ / "config" / "config.yaml"), help="de onde vem ai.prices (só essa chave)")
    p.add_argument("--dsn", help="PostgreSQL; no lugar de --db")
    p.add_argument("--origem", help="só uma origem de ai_calls (ex.: execucao); padrão: todas")
    p.add_argument("--por-motivo", action="store_true", help="quebra cada grupo pelo motivo do RA-10")
    p.add_argument("--minimo", type=int, default=20, help="abaixo disto, em qualquer lado, a amostra é 'pequena'")
    p.add_argument("--json", help="arquivo do JSON; padrão: a tela")
    p.add_argument("--md", help="arquivo do Markdown")
    args = p.parse_args(argv)
    corte, fonte = resolver_corte(args.corte)
    try:
        desde = _data(args.desde) if args.desde else None
        ate = _data(args.ate) if args.ate else None
    except ValueError as exc:
        raise SystemExit(f"data inválida (ISO-8601 UTC): {exc}") from exc
    db = _abrir(args)
    try:
        tabela, fonte_dos_precos = precos(Path(args.config))
        rel = montar(db, corte=corte, fonte=fonte, desde=desde, ate=ate, agora=datetime.now(UTC), origem=args.origem,
                     minimo=args.minimo, por_motivo=args.por_motivo, tabela=tabela, fonte_dos_precos=fonte_dos_precos)
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
