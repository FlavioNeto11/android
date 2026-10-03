"""Os grupos de `/api/usage` que a migração 080 tornou possíveis (RA-10 da reavaliação de 03/10).

Respondem às perguntas que `ai_calls` não respondia:
- quanto custa cada ORIGEM (execução, curador, decisão fechada…);
- o rejulgamento (7.10 e 17.10): quanto custa e quanto DISCORDA do barato, por app (o RA-16 decide amostrar com isto);
- a cascata do bloqueio (17.10): quantas vezes o modelo forte desbloqueou a tela que o barato largou;
- por que as decisões subiram de modelo, e por que a imagem foi junto;
- quantas etapas com decisão de IA ainda terminam sem `driven_by` (o aceite do RA-10 é zero).

O preço é o de `costs.usd_por`, que é a regra de `spent_usd`. As linhas anteriores à 080 têm as colunas nulas e não
entram em grupo nenhum: os números valem do deploy em diante.
"""
from __future__ import annotations

from collections.abc import Callable

from ..db import Database
from ..planning import costs
from .projecao import app_da_etapa

#: Discordância do rejulgamento: o modelo forte desfez o veredito do barato. No 7.10 o barato recusou (`no` ou
#: `uncertain`) vendo um nível que atende, e o forte aprova; no 17.10 o barato aprovou, e o forte não.
_DISCORDA: dict[str, Callable[[str | None], bool]] = {"nivel": lambda v: v == "yes",
                                                     "sim_com_efeito": lambda v: v != "yes"}


def _grupo(n: int, usd: float) -> dict[str, float | int]:
    return {"calls": n, "usd": round(usd, 4)}


def grupos(db: Database, prices: dict[str, list[float]], *, run_id: str | None, desde: str | None) -> dict[str, object]:
    """Os grupos do RA-10 para a janela de `/api/usage`: uma execução (`run_id`) ou desde um instante (`desde`)."""
    where, params = ("run_id=?", (run_id,)) if run_id else ("ts >= ?", (desde,))
    por_origem = costs.usd_por(db, prices, "origem", where, params)
    escalonadas = costs.usd_por(db, prices, "escalate", f"{where} AND escalate IS NOT NULL", params)
    rejulgadas = costs.usd_por(db, prices, "escalate", f"{where} AND role='verify' AND motivo='rejulgamento'", params)
    cascata = costs.usd_por(db, prices, "verdict", f"{where} AND role='decide' AND motivo='cascata'", params)
    imagem = db.query(f"SELECT image_reason, COUNT(*) n, SUM(with_image) com_imagem FROM ai_calls WHERE {where}"
                      " AND image_reason IS NOT NULL GROUP BY image_reason", params)
    return {
        "by_origin": {origem or "sem_origem": _grupo(n, usd) for origem, (n, usd) in sorted(
            por_origem.items(), key=lambda kv: kv[0] or "")},
        "escalations": {motivo: _grupo(n, usd) for motivo, (n, usd) in sorted(escalonadas.items())
                        if motivo is not None},
        "rejudges": _rejulgamentos(db, rejulgadas, run_id=run_id, desde=desde),
        "cascades": {**_grupo(sum(n for n, _ in cascata.values()), sum(u for _, u in cascata.values())),
                     # o modelo forte decidiu outra coisa que não "bloqueado": a pessoa não foi acordada à toa
                     "unblocked": sum(n for v, (n, _) in cascata.items() if v not in (None, "step_blocked")),
                     "by_verdict": {v or "erro": n
                                    for v, (n, _) in sorted(cascata.items(), key=lambda kv: kv[0] or "")}},
        "image_reasons": {r["image_reason"]: {"calls": int(r["n"]), "with_image": int(r["com_imagem"] or 0)}
                          for r in imagem},
        "steps_driven_by_null": _etapas_sem_condutor(db, run_id=run_id, desde=desde),
    }


def _rejulgamentos(db: Database, por_motivo: dict[str | None, tuple[int, float]], *, run_id: str | None,
                   desde: str | None) -> dict[str, object]:
    """Custo do rejulgamento e a discordância, no total e por app (o app da etapa, como no histórico das ações)."""
    where, params = ("c.run_id=?", (run_id,)) if run_id else ("c.ts >= ?", (desde,))
    linhas = db.query(
        f"SELECT c.escalate, c.verdict, s.app_id, r.app_ids FROM ai_calls c LEFT JOIN steps s ON s.id=c.step_id"
        f" LEFT JOIN runs r ON r.id=c.run_id WHERE {where} AND c.role='verify' AND c.motivo='rejulgamento'"
        " AND c.ok=1", params)
    por_app: dict[str, list[int]] = {}
    for linha in linhas:
        regra = _DISCORDA.get(linha["escalate"] or "")
        if regra is None:
            continue
        conta = por_app.setdefault(app_da_etapa(linha["app_id"], linha["app_ids"]), [0, 0])
        conta[0] += 1
        conta[1] += int(regra(linha["verdict"]))
    julgadas = sum(c[0] for c in por_app.values())
    discordou = sum(c[1] for c in por_app.values())
    return {**_grupo(sum(n for n, _ in por_motivo.values()), sum(u for _, u in por_motivo.values())),
            "by_kind": {k: _grupo(n, u) for k, (n, u) in sorted(por_motivo.items(), key=lambda kv: kv[0] or "")
                        if k is not None},
            "judged": julgadas, "disagreements": discordou,
            "disagreement_rate": round(discordou / julgadas, 4) if julgadas else None,
            "by_app": {app: {"judged": n, "disagreements": d, "disagreement_rate": round(d / n, 4)}
                       for app, (n, d) in sorted(por_app.items())}}


def _etapas_sem_condutor(db: Database, *, run_id: str | None, desde: str | None) -> int:
    """Etapas TERMINADAS com decisão de IA e `driven_by` nulo. `/api/usage` soma `driven_by` com COALESCE para `ai`, o
    que escondia exatamente este caso; o aceite do RA-10 é zero nas linhas novas."""
    where, params = ("s.run_id=?", (run_id,)) if run_id else ("c.ts >= ?", (desde,))
    linha = db.one(
        f"SELECT COUNT(DISTINCT s.id) n FROM steps s JOIN ai_calls c ON c.step_id=s.id AND c.role='decide'"
        f" WHERE {where} AND s.driven_by IS NULL"
        " AND s.status IN ('succeeded','failed','uncertain','waiting_user')", params)
    return int(linha["n"] or 0) if linha is not None else 0
