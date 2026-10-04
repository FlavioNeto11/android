"""31.56: o veredito MECÂNICO do A/B ao vivo da barra tapada (`ai.tapar_barra_de_endereco`), pré-registrado no código.

O A/B roda no android-09, em sites públicos e só leitura, depois do deploy 33. O braço desligado usa as chaves
`lote:jev:31.56-off-NN`; o ligado, `lote:jev:31.56-on-NN`. Este script só LÊ o banco: abre o SQLite por URI `mode=ro`,
com `PRAGMA query_only`, sem IA e sem escrever nada. Cada execução das chaves entra no relatório com:

- o sucesso (`runs.status = 'completed'`);
- as decisões por etapa (`ai_calls.role = 'decide'` ÷ etapas que rodaram);
- os tokens;
- a latência por etapa (`steps.finished_at − started_at`, posto mais próximo de `app.metricas.percentil`);
- o custo pela regra única do `planning/costs.spent_usd`.

O veredito sai do CRITÉRIO abaixo, escrito ANTES de qualquer execução (orquestradora, 04/10 22:49Z; critério do 31.56
nas mensagens das 22:42Z; bordas da revisão do #338 às 23:25Z, ainda antes de qualquer execução). Ele não se ajusta
depois de ver o número.

Uso, a partir da raiz do checkout:

    backend/.venv/Scripts/python.exe scripts/jev-veredito-31-56.py [--db C:/git/android/data/poc.sqlite3]
        [--config C:/git/android/config/config.yaml] [--json saida.json]

A saída leva só números e ids opacos: nenhum comando, texto de tela ou URL.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

import yaml

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

from app.config import AiCfg  # noqa: E402
from app.metricas import percentil  # noqa: E402 - o posto mais próximo do backend (K-085, 31.60)
from app.models import RUN_TERMINAL  # noqa: E402 - os estados terminais da execução, sem cópia
from app.planning.costs import spent_usd  # noqa: E402 - a regra única do custo

#: As chaves do A/B. `NN` é o par: a execução N do braço desligado e a do ligado usam o mesmo pedido.
CHAVE: Final = re.compile(r"^lote:jev:31\.56-(off|on)-(\d{2})$")
#: O critério PRÉ-REGISTRADO. Liga a chave só se as duas condições valem sobre os PARES COMPLETOS.
CRITERIO: Final = {
    "sucesso": "a taxa de sucesso do braço ligado é igual ou maior que a do desligado",
    "decisoes_por_etapa_a_mais_no_maximo": 0.20,
    "decisoes_com_desligado_em_zero": "desligado em 0 decisão por etapa e ligado acima de 0 é nao_liga; os dois em 0 é"
                                      " igual (0 % a mais)",
    "razao": "decisões por etapa de cada braço pelas somas cruas (decisões ÷ etapas), em fração exata; nada se"
             " arredonda antes da comparação",
    "pares": "o veredito usa só os pares completos (o mesmo NN com exatamente uma execução off e uma on); os outros vão"
             " para `pares_incompletos` e ficam fora",
    "minimo_de_pares_completos": 3,
    "cancelada": "execução cancelada (à mão ou pelo teto de US$ 0,25 por execução) conta como insucesso do braço dela",
    "inconclusivo": "menos pares completos que o mínimo, execução de par completo ainda rodando, ou braço sem etapa que"
                    " rodou com o sucesso do ligado igual ou maior",
    "ordem": "o sucesso decide primeiro: ligado com menos sucesso que o desligado é nao_liga, com ou sem etapa; só"
             " depois o braço sem etapa dá inconclusivo (os dois sem etapa também); por fim, as decisões por etapa",
    "chave_repetida": "a chave é única: repetir o disparo devolve a execução original e não cria outra; cada par precisa"
                      " de NN novo, e quem dispara confere que cada chave criou uma execução nova",
    "pre_registrado": "2026-10-04 22:49Z (orquestradora), antes de qualquer execução do 31.56; bordas V1 a V3 da revisão"
                      " às 23:25Z, também antes de qualquer execução",
}
TERMINAIS: Final = frozenset(s.value for s in RUN_TERMINAL)
LIMITE: Final = Fraction(1, 5)          # os 20 % do critério, exatos


class _Leitura:
    """O `db` que `spent_usd` lê (`query`), sobre a conexão só leitura."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(params)).fetchall()


def abrir(caminho: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{caminho.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def precos(caminho: Path | None) -> tuple[dict[str, list[float]], str]:
    """`ai.prices` do YAML desta instalação (só essa chave) e de onde veio: `config`, ou `padrao` (o do código) sem ele."""
    if caminho is not None and caminho.is_file():
        dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
        tabela = (dados.get("ai") or {}).get("prices") if isinstance(dados, dict) else None
        if isinstance(tabela, dict) and tabela:
            return {str(k): [float(x) for x in v] for k, v in tabela.items()}, "config"
    return {k: list(v) for k, v in AiCfg().prices.items()}, "padrao"


def _ms(inicio: str | None, fim: str | None) -> float | None:
    if not inicio or not fim:
        return None
    a = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
    b = datetime.fromisoformat(fim.replace("Z", "+00:00"))
    return (b - a).total_seconds() * 1000


def execucoes(conn: sqlite3.Connection, tabela: Mapping[str, list[float]]) -> tuple[list[dict[str, Any]], int]:
    """Uma linha por execução das chaves do 31.56, só números e ids; e quantas chaves do prefixo ficaram fora do padrão."""
    leitura = _Leitura(conn)
    saida = []
    ignoradas = 0
    for r in conn.execute("SELECT id, idempotency_key, status FROM runs WHERE idempotency_key LIKE 'lote:jev:31.56-%'"
                          " ORDER BY idempotency_key, id"):
        casou = CHAVE.match(str(r["idempotency_key"]))
        if casou is None:
            ignoradas += 1
            continue
        etapas = conn.execute("SELECT s.started_at, s.finished_at FROM steps s WHERE s.run_id=? AND EXISTS"
                              " (SELECT 1 FROM attempts a WHERE a.step_id=s.id)", (r["id"],)).fetchall()
        chamadas = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(input_tokens + output_tokens + COALESCE(cache_read, 0)"
                                " + COALESCE(cache_write, 0)), 0) t FROM ai_calls WHERE run_id=? AND role='decide'",
                                (r["id"],)).fetchone()
        tokens = conn.execute("SELECT COALESCE(SUM(input_tokens + output_tokens + COALESCE(cache_read, 0)"
                              " + COALESCE(cache_write, 0)), 0) FROM ai_calls WHERE run_id=?", (r["id"],)).fetchone()[0]
        latencias = sorted(ms for e in etapas if (ms := _ms(e["started_at"], e["finished_at"])) is not None)
        saida.append({
            "run": str(r["id"]), "braco": casou.group(1), "par": casou.group(2), "status": str(r["status"]),
            "terminou": str(r["status"]) in TERMINAIS, "sucesso": str(r["status"]) == "completed",
            "etapas": len(etapas), "decisoes": int(chamadas["n"]),
            "decisoes_por_etapa": round(chamadas["n"] / len(etapas), 3) if etapas else None,
            "tokens": int(tokens), "tokens_do_ator": int(chamadas["t"]),
            "latencia_por_etapa_ms": {"p50": percentil(latencias, 50), "p95": percentil(latencias, 95)},
            "usd": spent_usd(leitura, dict(tabela), run_id=str(r["id"])),
        })
    return saida, ignoradas


def _braco(linhas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    decisoes = sum(ln["decisoes"] for ln in linhas if ln["etapas"])
    etapas = sum(ln["etapas"] for ln in linhas)
    sucessos = sum(1 for ln in linhas if ln["sucesso"])
    return {
        "execucoes": len(linhas), "sucessos": sucessos,
        "taxa_de_sucesso": round(sucessos / len(linhas), 3) if linhas else None,
        "decisoes": decisoes, "etapas": etapas,
        "decisoes_por_etapa": round(decisoes / etapas, 3) if etapas else None,
        "tokens": sum(ln["tokens"] for ln in linhas), "usd": round(sum(ln["usd"] for ln in linhas), 6),
    }


def pares(linhas: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """Os pares completos (exatamente uma `off` e uma `on`) e os incompletos, com quantas de cada braço têm."""
    por_par: dict[str, dict[str, int]] = {}
    for ln in linhas:
        por_par.setdefault(ln["par"], {"off": 0, "on": 0})[ln["braco"]] += 1
    completos = sorted(p for p, n in por_par.items() if n == {"off": 1, "on": 1})
    incompletos = [{"par": p, **n} for p, n in sorted(por_par.items()) if p not in completos]
    return completos, incompletos


def veredito(linhas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """`liga`, `nao_liga` ou `inconclusivo`, pelo `CRITERIO`, com o porquê em números."""
    completos, incompletos = pares(linhas)
    dentro = [ln for ln in linhas if ln["par"] in completos]
    off = _braco([ln for ln in dentro if ln["braco"] == "off"])
    on = _braco([ln for ln in dentro if ln["braco"] == "on"])
    resumo: dict[str, Any] = {"criterio": CRITERIO, "pares_completos": completos, "pares_incompletos": incompletos,
                              "desligado": off, "ligado": on}
    if len(completos) < CRITERIO["minimo_de_pares_completos"]:
        return {**resumo, "veredito": "inconclusivo",
                "porque": f"{len(completos)} par(es) completo(s); o mínimo é {CRITERIO['minimo_de_pares_completos']}"}
    if not all(ln["terminou"] for ln in dentro):
        return {**resumo, "veredito": "inconclusivo", "porque": "execução de par completo ainda não terminada"}
    sucesso_ok = Fraction(on["sucessos"], on["execucoes"]) >= Fraction(off["sucessos"], off["execucoes"])
    if not sucesso_ok:
        # N1 da revisão: o sucesso decide antes da contagem de decisões. O ligado que falha sempre antes da 1ª etapa
        # já perdeu pelo sucesso; "sem etapa" não o salva para `inconclusivo`.
        return {**resumo, "veredito": "nao_liga",
                "porque": {"sucesso_igual_ou_maior": False, "decisoes_dentro_do_limite": None}}
    if not off["etapas"] or not on["etapas"]:
        return {**resumo, "veredito": "inconclusivo", "porque": "braço sem etapa que rodou"}
    taxa_off = Fraction(off["decisoes"], off["etapas"])
    taxa_on = Fraction(on["decisoes"], on["etapas"])
    if taxa_off == 0:
        a_mais: Fraction | None = Fraction(0) if taxa_on == 0 else None      # V1: de 0 para mais que 0 é sem limite
    else:
        a_mais = taxa_on / taxa_off - 1
    decisoes_ok = a_mais is not None and a_mais <= LIMITE
    resumo["decisoes_por_etapa_a_mais"] = round(float(a_mais), 3) if a_mais is not None else None
    return {**resumo, "veredito": "liga" if (sucesso_ok and decisoes_ok) else "nao_liga",
            "porque": {"sucesso_igual_ou_maior": sucesso_ok, "decisoes_dentro_do_limite": decisoes_ok}}


def main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", default="C:/git/android/data/poc.sqlite3")
    p.add_argument("--config", default=str(RAIZ / "config" / "config.yaml"), help="de onde vem ai.prices (só essa chave)")
    p.add_argument("--json", help="grava o relatório completo aqui (só números e ids)")
    a = p.parse_args(argv)
    if not Path(a.db).is_file():
        print(f"banco não encontrado: {a.db}", file=sys.stderr)
        raise SystemExit(2)
    tabela, origem = precos(Path(a.config))
    conn = abrir(Path(a.db))
    try:
        linhas, ignoradas = execucoes(conn, tabela)
    finally:
        conn.close()
    relatorio = {"precos": origem, "chaves_ignoradas": ignoradas, "execucoes": linhas, **veredito(linhas)}
    texto = json.dumps(relatorio, ensure_ascii=False, indent=1)
    if a.json:
        Path(a.json).write_text(texto, encoding="utf-8")
    print(texto)
    return relatorio


if __name__ == "__main__":
    main()
