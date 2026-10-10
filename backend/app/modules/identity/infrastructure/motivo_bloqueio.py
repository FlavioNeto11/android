"""O motivo de uma conta bloqueada, montado como DADO (31.322).

A conta que cai na tela humana sai da plataforma (ADR-068), e com ela vai a linha de `contas_igfarm` que dizia por
qual IP ela nasceu. Esta rotina lê isso ANTES da retirada e devolve um objeto pequeno, sem segredo e sem o @, que
fica na lápide e no evento `profile.account_retired`:

- `egresso_esperado`: o IP da criação (o que o aparelho deveria ter usado);
- `egresso_medido`: a última saída medida de um aparelho onde a conta tinha sessão;
- `egresso_divergente`: esperado e medido diferem (só quando os dois existem);
- `ips_distintos_desde_criacao`: quantas saídas diferentes o aparelho mediu depois da criação (rotação);
- `minutos_ate_o_primeiro_login`: da criação no igfarm à primeira tentativa de login;
- `trecho_da_tela`: o trecho curto da evidência, já sem o rastro da conta.

Campo que não se sabe fica `None`: nunca se inventa. Quem chama trata a falha como "sem motivo".
"""
from __future__ import annotations

from datetime import datetime

from app.db import Database


def _t(valor: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def motivo_do_bloqueio(db: Database, profile_id: str, account_id: str, evidencia: str | None) -> dict[str, object]:
    criada = db.one("SELECT criada_em_igfarm, ip_criacao FROM contas_igfarm WHERE account_id=?", (account_id,))
    esperado = str(criada["ip_criacao"]) if criada is not None and criada["ip_criacao"] else None
    criada_em = str(criada["criada_em_igfarm"]) if criada is not None and criada["criada_em_igfarm"] else None
    aparelhos = [str(r["instance_id"]) for r in db.query(
        "SELECT instance_id FROM account_sessions WHERE account_id=?", (account_id,))]
    medido: str | None = None
    distintos: int | None = None
    if aparelhos:
        marcas = ",".join("?" for _ in aparelhos)
        ultima = db.one(f"SELECT egress_ipv4 FROM network_measurements WHERE instance_id IN ({marcas})"
                        " AND egress_ipv4 IS NOT NULL ORDER BY measured_at DESC, id DESC LIMIT 1", tuple(aparelhos))
        medido = str(ultima["egress_ipv4"]) if ultima is not None else None
        if criada_em:
            distintos = int(db.scalar(
                f"SELECT COUNT(DISTINCT egress_ipv4) FROM network_measurements WHERE instance_id IN ({marcas})"
                " AND egress_ipv4 IS NOT NULL AND measured_at >= ?", (*aparelhos, criada_em)) or 0)
    minutos: float | None = None
    primeiro = db.scalar("SELECT MIN(started_at) FROM authentication_attempts WHERE account_id=?", (account_id,))
    if criada_em and primeiro and (a := _t(criada_em)) is not None and (b := _t(primeiro)) is not None:
        minutos = round((b - a).total_seconds() / 60, 1)
    return {
        "egresso_esperado": esperado,
        "egresso_medido": medido,
        "egresso_divergente": (esperado != medido) if esperado and medido else None,
        "ips_distintos_desde_criacao": distintos,
        "minutos_ate_o_primeiro_login": minutos,
        "trecho_da_tela": (evidencia or "")[:200] or None,
    }
