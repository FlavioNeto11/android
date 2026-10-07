"""31.229 (adendo v1.124): o custo e o modelo por passo de uma execução, a fonte da linha do tempo do alvo (31.228) e
da medida da política de modelos (31.223). Só lê; não muda a política.

O vínculo já vem da origem: o executor grava `ai_calls.step_id` em toda chamada `decide`/`verify` de uma etapa, e
`actions.ai_call_id` (088) aponta para o `decide` que escolheu a ação. O planejamento não tem etapa e vai em
`sem_passo`. O custo segue a regra de `costs.spent_usd` (declarado onde há, tokens x preço onde não, simulado a US$ 0),
e `chamadas` conta todas, como em `costs.usd_por`. São quatro consultas por leitura, para qualquer número de execuções.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from ..db import Database
from . import costs

SEM_PASSO = "sem_passo"
SEM_ESTAGIO = "sem_estagio"


@dataclass
class _Soma:
    chamadas: int = 0
    usd: float = 0.0
    por_modelo: dict[str, list[float]] = field(default_factory=dict)   # modelo → [chamadas, usd]

    def somar(self, modelo: str, chamadas: int, usd: float) -> None:
        self.chamadas += chamadas
        self.usd += usd
        atual = self.por_modelo.setdefault(modelo, [0, 0.0])
        atual[0] += chamadas
        atual[1] += usd

    def juntar(self, outra: _Soma) -> None:
        for modelo, (n, usd) in outra.por_modelo.items():
            self.somar(modelo, int(n), usd)

    def modelos(self) -> list[dict[str, object]]:
        return [{"modelo": m, "chamadas": int(n), "custo_usd": round(usd, 6)}
                for m, (n, usd) in sorted(self.por_modelo.items(), key=lambda kv: (-kv[1][1], kv[0]))]

    def resumo(self) -> dict[str, object]:
        return {"chamadas": self.chamadas, "custo_usd": round(self.usd, 6)}


def por_execucao(db: Database, prices: dict[str, list[float]], run_ids: Sequence[str],
                 estagios: Mapping[str, str]) -> dict[str, dict[str, object]]:
    """`custo_por_passo` de cada execução (o objeto do adendo v1.124). `estagios`: capability → estágio da operação
    (`AppDefinition.operation_stages`); a capability fora dele fica em `sem_estagio`."""
    ids = list(dict.fromkeys(str(r) for r in run_ids if r))
    if not ids:
        return {}
    marcas = ",".join("?" * len(ids))
    params = tuple(ids)
    passos = db.query(f"SELECT id, run_id, seq, key, capability, side_effect FROM steps WHERE run_id IN ({marcas})"
                      " ORDER BY run_id, seq, plan_version, id", params)
    grupos = db.query(
        f"SELECT run_id, step_id, model, COALESCE(provider,'') = 'simulated' simulado, COUNT(*) n,"
        f" SUM(CASE WHEN usd IS NULL THEN input_tokens ELSE 0 END) input_tokens,"
        f" SUM(CASE WHEN usd IS NULL THEN cache_read ELSE 0 END) cache_read,"
        f" SUM(CASE WHEN usd IS NULL THEN cache_write ELSE 0 END) cache_write,"
        f" SUM(CASE WHEN usd IS NULL THEN COALESCE(cache_write_1h, 0) ELSE 0 END) cache_write_1h,"
        f" SUM(CASE WHEN usd IS NULL THEN output_tokens ELSE 0 END) output_tokens,"
        f" SUM(COALESCE(usd, 0)) usd_declarado FROM ai_calls WHERE run_id IN ({marcas})"
        f" GROUP BY run_id, step_id, model, COALESCE(provider,'') = 'simulated'", params)
    decisores = db.query(f"SELECT step_id, model FROM ai_calls WHERE run_id IN ({marcas}) AND role='decide' AND ok=1"
                         " AND step_id IS NOT NULL ORDER BY id", params)
    commits = db.query(
        f"SELECT t.step_id, a.source, c.model, c.tier, c.escalate FROM actions a JOIN attempts t ON t.id=a.attempt_id"
        f" JOIN steps s ON s.id=t.step_id LEFT JOIN ai_calls c ON c.id=a.ai_call_id"
        f" WHERE s.run_id IN ({marcas}) AND a.side_effect=1 AND a.status<>'rejected' ORDER BY a.id", params)

    modelo_do_passo = {str(r["step_id"]): str(r["model"]) for r in decisores}   # o último decide ok vence
    # O commit é a última ação com efeito não rejeitada (a decisão descartada fica sem ação ligada); `tier` e
    # `escalate` dizem se ela veio do modelo escalonado para o efeito (31.223: 1 e 'efeito').
    commit_do_passo = {str(r["step_id"]): {"fonte": "recipe" if r["source"] == "recipe" else "ai",
                                           "modelo": str(r["model"]) if r["model"] else None,
                                           "tier": int(r["tier"]) if r["tier"] is not None else None,
                                           "escalate": r["escalate"]} for r in commits}
    run_do_passo = {str(p["id"]): str(p["run_id"]) for p in passos}
    soma_do_passo: dict[str, _Soma] = {}
    sem_passo: dict[str, _Soma] = {r: _Soma() for r in ids}
    for g in grupos:
        usd = 0.0 if g["simulado"] else costs.row_usd(prices, g) + float(g["usd_declarado"] or 0)
        passo = str(g["step_id"]) if g["step_id"] else None
        if passo is not None and run_do_passo.get(passo) == str(g["run_id"]):
            soma_do_passo.setdefault(passo, _Soma()).somar(str(g["model"]), int(g["n"]), usd)
        else:
            sem_passo[str(g["run_id"])].somar(str(g["model"]), int(g["n"]), usd)

    saida: dict[str, dict[str, object]] = {}
    for rid in ids:
        lista: list[dict[str, object]] = []
        total, por_estagio = _Soma(), {}
        for p in passos:
            if str(p["run_id"]) != rid:
                continue
            sid = str(p["id"])
            soma = soma_do_passo.get(sid, _Soma())
            estagio = estagios.get(str(p["capability"])) if p["capability"] else None
            lista.append({"step_id": sid, "seq": int(p["seq"]), "key": p["key"], "capability": p["capability"],
                          "efeito": bool(p["side_effect"]), "estagio": estagio,
                          "modelo": modelo_do_passo.get(sid), "commit": commit_do_passo.get(sid), **soma.resumo(),
                          "por_modelo": soma.modelos()})
            total.juntar(soma)
            por_estagio.setdefault(estagio or SEM_ESTAGIO, _Soma()).juntar(soma)
        fora = sem_passo[rid]
        total.juntar(fora)
        if fora.chamadas:
            por_estagio.setdefault(SEM_PASSO, _Soma()).juntar(fora)
        saida[rid] = {"passos": lista, "sem_passo": {**fora.resumo(), "por_modelo": fora.modelos()},
                      "por_modelo": total.modelos(),
                      "por_estagio": {e: s.resumo() for e, s in por_estagio.items() if s.chamadas}}
    return saida


def somar(leituras: Iterable[Mapping[str, object] | None]) -> tuple[list[dict[str, object]], dict[str, object]]:
    """`custo_por_modelo` e `custo_por_estagio` da operação: as listas e os mapas dos alvos, somados."""
    total = _Soma()
    estagios: dict[str, _Soma] = {}
    for leitura in leituras:
        if not leitura:
            continue
        for item in leitura.get("por_modelo") or []:  # type: ignore[attr-defined]
            total.somar(str(item["modelo"]), int(item["chamadas"]), float(item["custo_usd"]))
        for estagio, s in (leitura.get("por_estagio") or {}).items():  # type: ignore[attr-defined]
            estagios.setdefault(str(estagio), _Soma()).somar("", int(s["chamadas"]), float(s["custo_usd"]))
    return total.modelos(), {e: s.resumo() for e, s in estagios.items()}
